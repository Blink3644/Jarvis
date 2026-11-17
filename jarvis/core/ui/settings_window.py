"""Settings window (Stage A4): tabs for General, Voice, PC, Notes, Routines, Minecraft, About.

Form logic (parsing/validation) is in plain functions at the top so it can be tested
without a screen. The widgets are below.

`hooks` is a dict or an object with optional callables (missing = control disabled):
    test_key(key) -> (ok, message)
    test_wake() -> None               (app starts a 10 s test; call window.wake_result(text))
    mic_level() -> float 0..1         (polled every 100 ms while the Voice tab is shown)
    reopen_mic() -> None              (called after the mic choice is saved and changed)
    apply(cfg) -> None                (called after Save)
    clear_history() -> None
    check_usage() -> list[(model, used, limit)]
    tool_names() -> list[str]         (for the routine step editor)
"""
import copy
import json
import logging
import os

from .. import config, paths, secrets

log = logging.getLogger(__name__)

NEW_DEFAULTS = {
    "start_with_windows": False,
    "startup_greeting": "simple",     # off | simple | brief
    "theme": "hud",                   # hud | minimal
}

AUTO_LOOK_LABELS = {"ask": "Only when asked", "games": "In games only", "always": "Always"}
REPLY_LABELS = {"spoken": "Spoken", "text": "On-screen text", "both": "Both"}
PERSONALITY_LABELS = {"butler": "Butler", "casual": "Casual friend", "direct": "Straight to the point"}
HISTORY_LABELS = {"session": "Session only (forget when Jarvis closes)",
                  "log": "Save a readable log (Jarvis doesn't reuse it)",
                  "remember": "Remember recent conversations (sends more data to Google)"}
GREETING_LABELS = {"off": "Off", "simple": "Simple greeting", "brief": "Daily brief"}
THEME_LABELS = {"hud": "HUD (glowing orb)", "minimal": "Minimal"}
VOICES = ["en-GB-RyanNeural", "en-GB-ThomasNeural", "en-GB-SoniaNeural", "en-US-GuyNeural",
          "en-US-AriaNeural", "en-US-ChristopherNeural"]
DEFAULT_DEVICE = "(Windows default)"


# ---------------------------------------------------------------- pure logic
def label_to_key(labels: dict, label: str, default=None):
    for k, v in labels.items():
        if v == label:
            return k
    return default if default is not None else next(iter(labels))


def parse_list(text: str) -> list[str]:
    """Comma- or newline-separated words -> clean list."""
    out = []
    for part in (text or "").replace("\n", ",").split(","):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def parse_pairs(rows) -> dict:
    """[(spoken, target), ...] -> dict, skipping blank rows."""
    out = {}
    for a, b in rows:
        a, b = (a or "").strip().lower(), (b or "").strip()
        if a and b:
            out[a] = b
    return out


def _num(raw, name, lo, hi, cast, errors):
    try:
        v = cast(str(raw).strip())
    except (TypeError, ValueError):
        errors.append(f"{name} must be a number.")
        return None
    if not lo <= v <= hi:
        errors.append(f"{name} must be between {lo} and {hi}.")
        return None
    return v


def build_cfg(base: dict, form: dict) -> tuple[dict, list[str]]:
    """Turn raw form values into a new cfg. Returns (cfg, errors). `base` is not modified.

    Only keys present in `form` are changed. 'daily_limits' is {model: "250"};
    'app_aliases' is a list of (spoken, target) pairs.
    """
    cfg = copy.deepcopy(base)
    errors: list[str] = []
    f = form
    for k in ("model_main", "model_fallback", "user_title", "home_city", "tts_voice", "ptt_key",
              "mic_device", "speaker_device"):
        if k in f:
            cfg[k] = str(f[k]).strip()
    for k in ("model_main", "model_fallback", "tts_voice", "ptt_key"):
        if k in f and not cfg[k]:
            errors.append(f"'{k.replace('_', ' ')}' can't be empty.")
    for k, allowed in (("personality", config.PERSONALITIES), ("history_mode", config.HISTORY_MODES),
                       ("reply_mode", config.REPLY_MODES), ("auto_look", tuple(AUTO_LOOK_LABELS)),
                       ("startup_greeting", tuple(GREETING_LABELS)), ("theme", tuple(THEME_LABELS))):
        if k in f:
            if f[k] not in allowed:
                errors.append(f"Unknown choice for {k}.")
            else:
                cfg[k] = f[k]
    for k in ("wake_enabled", "ptt_enabled", "stop_word_enabled", "start_with_windows"):
        if k in f:
            cfg[k] = bool(f[k])
    if "wake_sensitivity" in f:
        v = _num(f["wake_sensitivity"], "Wake word sensitivity", 0.1, 0.9, float, errors)
        if v is not None:
            cfg["wake_sensitivity"] = round(v, 2)
    if "followup_seconds" in f:
        v = _num(f["followup_seconds"], "Follow-up listening seconds", 0, 30, float, errors)
        if v is not None:
            cfg["followup_seconds"] = v
    if "listen_default_s" in f or "listen_max_s" in f:
        d = _num(f.get("listen_default_s", cfg["listen_default_s"]), "Listen length", 1, 600, int, errors)
        m = _num(f.get("listen_max_s", cfg["listen_max_s"]), "Longest listen", 1, 600, int, errors)
        if d is not None and m is not None:
            if d > m:
                errors.append("The default listen length can't be longer than the maximum.")
            else:
                cfg["listen_default_s"], cfg["listen_max_s"] = d, m
    if "daily_limits" in f:
        lims = {}
        for model, raw in f["daily_limits"].items():
            v = _num(raw, f"Daily limit for {model}", 1, 1_000_000, int, errors)
            if v is not None:
                lims[model] = v
        if lims:
            cfg["daily_limits"] = {**cfg.get("daily_limits", {}), **lims}
    if "app_aliases" in f:
        cfg["app_aliases"] = parse_pairs(f["app_aliases"])
    for k in ("game_list", "never_screenshot"):
        if k in f:
            cfg[k] = f[k] if isinstance(f[k], list) else parse_list(f[k])
    return cfg, errors


def mask_key(key: str | None) -> str:
    if not key:
        return "No key saved yet"
    return f"Key saved (...{key[-4:]})"


def format_usage(rows) -> list[str]:
    out = []
    for model, used, limit in rows:
        pct = f" ({used * 100 // limit}%)" if limit else ""
        out.append(f"{model}: {used} of {limit or '?'} today{pct}")
    return out


def parse_args_text(text: str) -> tuple[dict, str | None]:
    """'level=40, name="chrome"' or a JSON object -> dict. Returns (args, error)."""
    text = (text or "").strip()
    if not text:
        return {}, None
    if text.startswith("{"):
        try:
            v = json.loads(text)
        except ValueError:
            return {}, "The options aren't valid JSON."
        return (v, None) if isinstance(v, dict) else ({}, "The options must be name=value pairs.")
    args = {}
    for part in text.split(","):
        if "=" not in part:
            return {}, f"'{part.strip()}' should look like name=value."
        k, v = part.split("=", 1)
        v = v.strip()
        try:
            v = json.loads(v)
        except ValueError:
            pass  # plain text stays a string
        args[k.strip()] = v
    return args, None


# ---------------------------------------------------------------- widgets
class _Hooks:
    def __init__(self, hooks):
        self._h = hooks or {}

    def get(self, name):
        if isinstance(self._h, dict):
            return self._h.get(name)
        return getattr(self._h, name, None)

    def call(self, name, *args, default=None):
        fn = self.get(name)
        if not callable(fn):
            return default
        try:
            return fn(*args)
        except Exception:
            log.exception("settings hook %s failed", name)
            return default


class _Rows:
    """Editable list of (a, b) text pairs."""

    def __init__(self, ctk, parent, pairs, ph_a, ph_b):
        self.ctk, self.ph_a, self.ph_b = ctk, ph_a, ph_b
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.rows = []
        for a, b in pairs:
            self.add(a, b)
        ctk.CTkButton(self.frame, text="Add row", width=90, command=self.add).pack(anchor="w", pady=2)

    def add(self, a="", b=""):
        ctk = self.ctk
        row = ctk.CTkFrame(self.frame, fg_color="transparent")
        row.pack(fill="x", pady=1)
        ea = ctk.CTkEntry(row, placeholder_text=self.ph_a, width=150)
        eb = ctk.CTkEntry(row, placeholder_text=self.ph_b)
        ea.pack(side="left", padx=2)
        eb.pack(side="left", padx=2, fill="x", expand=True)
        if a:
            ea.insert(0, a)
        if b:
            eb.insert(0, b)
        item = (row, ea, eb)
        ctk.CTkButton(row, text="X", width=28, command=lambda: self._remove(item)).pack(side="left")
        self.rows.append(item)

    def _remove(self, item):
        item[0].destroy()
        self.rows.remove(item)

    def values(self):
        return [(ea.get(), eb.get()) for _, ea, eb in self.rows]

    def pack(self, **kw):
        self.frame.pack(**kw)


class SettingsWindow:
    """SettingsWindow(root_or_None, cfg, hooks).

    Builds a CTkToplevel (or a CTk window when root is None). Useful members:
    .win (the window), .vars (name -> tk variable), .collect_form(), .save(), .close(),
    .show(), .wake_result(text).
    """

    def __init__(self, root, cfg, hooks=None, *, hidden=False):
        import customtkinter as ctk
        self.ctk = ctk
        self.cfg = copy.deepcopy(cfg)
        for k, v in NEW_DEFAULTS.items():
            self.cfg.setdefault(k, v)
        self.h = _Hooks(hooks)
        self.win = ctk.CTkToplevel(root) if root is not None else ctk.CTk()
        self.win.title("Jarvis settings")
        self.win.geometry("720x620")
        self.vars = {}
        self._meter_job = None
        self._on_voice_tab = False
        self.result = None
        self._build()
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        if hidden:
            self.win.withdraw()

    # ---- helpers
    def _var(self, name, value, kind="str"):
        tk = {"str": self.ctk.StringVar, "bool": self.ctk.BooleanVar, "float": self.ctk.DoubleVar}[kind]
        v = tk(value=value)
        self.vars[name] = v
        return v

    def _label(self, parent, text, bold=False, wrap=620, color=None):
        kw = {"text": text, "anchor": "w", "justify": "left", "wraplength": wrap}
        if bold:
            kw["font"] = self.ctk.CTkFont(weight="bold")
        if color:
            kw["text_color"] = color
        lab = self.ctk.CTkLabel(parent, **kw)
        lab.pack(fill="x", padx=8, pady=(6, 0))
        return lab

    def _menu(self, parent, name, labels, current_key, caption):
        self._label(parent, caption)
        v = self._var(name, labels.get(current_key, list(labels.values())[0]))
        self.ctk.CTkOptionMenu(parent, variable=v, values=list(labels.values())).pack(anchor="w", padx=8)
        return v

    def _entry(self, parent, name, value, caption, width=300):
        self._label(parent, caption)
        v = self._var(name, str(value))
        e = self.ctk.CTkEntry(parent, textvariable=v, width=width)
        e.pack(anchor="w", padx=8)
        return e

    def _check(self, parent, name, value, text):
        v = self._var(name, bool(value), "bool")
        self.ctk.CTkCheckBox(parent, text=text, variable=v).pack(anchor="w", padx=8, pady=4)
        return v

    # ---- build
    def _build(self):
        ctk = self.ctk
        self.tabs = ctk.CTkTabview(self.win, command=self._tab_changed)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        for name in ("General", "Voice", "PC", "Notes", "Routines", "Minecraft", "About"):
            self.tabs.add(name)
        self._general(self.tabs.tab("General"))
        self._voice(self.tabs.tab("Voice"))
        self._pc(self.tabs.tab("PC"))
        self._notes(self.tabs.tab("Notes"))
        self._routines(self.tabs.tab("Routines"))
        self._minecraft(self.tabs.tab("Minecraft"))
        self._about(self.tabs.tab("About"))
        bar = ctk.CTkFrame(self.win, fg_color="transparent")
        bar.pack(fill="x", padx=8, pady=8)
        self.status = ctk.CTkLabel(bar, text="", anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(bar, text="Cancel", width=90, fg_color="gray40", command=self.close).pack(side="right", padx=4)
        ctk.CTkButton(bar, text="Save", width=90, command=self.save).pack(side="right", padx=4)

    def _scroll(self, tab):
        f = self.ctk.CTkScrollableFrame(tab)
        f.pack(fill="both", expand=True)
        return f

    def _general(self, tab):
        ctk, c = self.ctk, self.cfg
        p = self._scroll(tab)
        self._label(p, "Google Gemini API key", bold=True)
        self.key_status = self._label(p, mask_key(secrets.get_api_key()))
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", padx=8)
        self.key_entry = ctk.CTkEntry(row, show="*", width=300, placeholder_text="Paste a new key here to change it")
        self.key_entry.pack(side="left")
        ctk.CTkButton(row, text="Test key", width=90, command=self._test_key,
                      state="normal" if self.h.get("test_key") else "disabled").pack(side="left", padx=6)
        self._entry(p, "model_main", c["model_main"], "Main model")
        self._entry(p, "model_fallback", c["model_fallback"], "Backup model (used when the main one runs out)")
        self._menu(p, "personality", PERSONALITY_LABELS, c["personality"], "Personality")
        self._entry(p, "user_title", c["user_title"], "What Jarvis calls you (for example sir, or your name)", width=200)
        self._menu(p, "reply_mode", REPLY_LABELS, c["reply_mode"], "How Jarvis replies")
        self._menu(p, "history_mode", HISTORY_LABELS, c["history_mode"], "Conversation history")
        ctk.CTkButton(p, text="Clear history", width=120, command=self._clear_history,
                      state="normal" if self.h.get("clear_history") else "disabled").pack(anchor="w", padx=8, pady=4)
        self._label(p, "Today's free requests (limits can be edited, Google changes them)", bold=True)
        self.usage_frame = ctk.CTkFrame(p, fg_color="transparent")
        self.usage_frame.pack(fill="x", padx=8)
        self._fill_usage()
        self._check(p, "start_with_windows", c["start_with_windows"], "Start Jarvis when Windows starts")
        self._menu(p, "startup_greeting", GREETING_LABELS, c["startup_greeting"], "Greeting at startup")
        self._menu(p, "theme", THEME_LABELS, c["theme"], "Look of the on-screen panel")

    def _fill_usage(self):
        ctk = self.ctk
        for w in self.usage_frame.winfo_children():
            w.destroy()
        used = {m: u for m, u, _ in (self.h.call("check_usage", default=None) or [])}
        self.limit_vars = {}
        limits = self.cfg.get("daily_limits", {})
        for model in dict.fromkeys([self.cfg["model_main"], self.cfg["model_fallback"], *limits]):
            u = used.get(model)
            r = ctk.CTkFrame(self.usage_frame, fg_color="transparent")
            r.pack(fill="x", pady=1)
            ctk.CTkLabel(r, text=f"{model}: used {'?' if u is None else u} of", anchor="w", width=300).pack(side="left")
            v = ctk.StringVar(value=str(limits.get(model, "")))
            ctk.CTkEntry(r, textvariable=v, width=80).pack(side="left")
            self.limit_vars[model] = v

    def _voice(self, tab):
        ctk, c = self.ctk, self.cfg
        p = self._scroll(tab)
        try:
            from ..voice import devices
            ins = [d["name"] for d in devices.list_inputs()]
            outs = [d["name"] for d in devices.list_outputs()]
        except Exception:
            log.exception("could not list audio devices")
            ins, outs = [], []
        self._label(p, "Microphone. Jarvis only listens to this microphone.", bold=True)
        v = self._var("mic_device", c["mic_device"] or DEFAULT_DEVICE)
        ctk.CTkOptionMenu(p, variable=v, values=[DEFAULT_DEVICE, *ins]).pack(anchor="w", padx=8)
        self.meter = ctk.CTkProgressBar(p, width=300)
        self.meter.set(0)
        self.meter.pack(anchor="w", padx=8, pady=6)
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", padx=8)
        ctk.CTkButton(row, text="Test wake word", width=130, command=self._test_wake,
                      state="normal" if self.h.get("test_wake") else "disabled").pack(side="left")
        self.wake_label = ctk.CTkLabel(row, text="", anchor="w")
        self.wake_label.pack(side="left", padx=8)
        self._label(p, "Speaker", bold=True)
        v = self._var("speaker_device", c["speaker_device"] or DEFAULT_DEVICE)
        ctk.CTkOptionMenu(p, variable=v, values=[DEFAULT_DEVICE, *outs]).pack(anchor="w", padx=8)
        self._check(p, "wake_enabled", c["wake_enabled"], 'Wake word ("Hey Jarvis")')
        self._label(p, "Wake word sensitivity (low = strict, high = picks up more)")
        v = self._var("wake_sensitivity", float(c["wake_sensitivity"]), "float")
        ctk.CTkSlider(p, from_=0.1, to=0.9, variable=v, width=300).pack(anchor="w", padx=8)
        self._check(p, "ptt_enabled", c["ptt_enabled"], "Push-to-talk (hold a key to speak)")
        self._entry(p, "ptt_key", c["ptt_key"], "Push-to-talk key (for example ctrl_r, alt_r, f8)", width=150)
        self._entry(p, "followup_seconds", c["followup_seconds"],
                    "Keep listening after a reply for this many seconds (0 = off)", width=80)
        self._check(p, "stop_word_enabled", c["stop_word_enabled"], 'Saying "stop" interrupts Jarvis')
        self._label(p, "Voice (pick one, or type any edge-tts voice name)")
        v = self._var("tts_voice", c["tts_voice"])
        ctk.CTkComboBox(p, variable=v, values=VOICES, width=300).pack(anchor="w", padx=8)

    def _pc(self, tab):
        ctk, c = self.ctk, self.cfg
        p = self._scroll(tab)
        self._entry(p, "home_city", c["home_city"], "Home city (for the weather)", width=200)
        self._label(p, "App nicknames: what you say, then the app name or path", bold=True)
        self.aliases = _Rows(ctk, p, list(c["app_aliases"].items()), "browser", "Google Chrome")
        self.aliases.pack(fill="x", padx=8)
        self._label(p, "Extra games (program names such as eldenring.exe, separated by commas)")
        self.games_box = ctk.CTkTextbox(p, height=60)
        self.games_box.pack(fill="x", padx=8)
        self.games_box.insert("1.0", ", ".join(c["game_list"]))
        self._menu(p, "auto_look", AUTO_LOOK_LABELS, c["auto_look"], "Let Jarvis look at your screen")
        self._label(p, 'Warning: "Always" sends whatever is on your screen to Google with every question.',
                    color="orange")
        self._label(p, "Never take a screenshot when the window name contains (separated by commas)")
        self.never_box = ctk.CTkTextbox(p, height=60)
        self.never_box.pack(fill="x", padx=8)
        self.never_box.insert("1.0", ", ".join(c["never_screenshot"]))
        self._entry(p, "listen_default_s", c["listen_default_s"], "Listening to PC audio: default seconds", width=80)
        self._entry(p, "listen_max_s", c["listen_max_s"], "Listening to PC audio: longest allowed seconds", width=80)

    # ---- notes
    def _notes(self, tab):
        self.notes_frame = self.ctk.CTkScrollableFrame(tab)
        self.notes_frame.pack(fill="both", expand=True)
        self._fill_notes()

    def _fill_notes(self):
        ctk = self.ctk
        for w in self.notes_frame.winfo_children():
            w.destroy()
        try:
            from jarvis.pc import notes
            items = notes.load_notes()
        except Exception:
            ctk.CTkLabel(self.notes_frame, text="Notes are not available.").pack(anchor="w")
            return
        if not items:
            ctk.CTkLabel(self.notes_frame, text='No notes yet. Say "remember ..." to Jarvis.').pack(anchor="w")
        for n in items:
            row = ctk.CTkFrame(self.notes_frame)
            row.pack(fill="x", pady=2)
            v = ctk.StringVar(value=n["text"])
            ctk.CTkEntry(row, textvariable=v).pack(side="left", fill="x", expand=True, padx=4)
            ctk.CTkLabel(row, text=f'{n.get("scope", "global")}  {str(n.get("created", ""))[:10]}',
                         width=130).pack(side="left")
            ctk.CTkButton(row, text="Save", width=56,
                          command=lambda i=n["id"], v=v: self._note_edit(i, v)).pack(side="left", padx=2)
            ctk.CTkButton(row, text="Delete", width=56, fg_color="firebrick",
                          command=lambda i=n["id"]: self._note_delete(i)).pack(side="left", padx=2)

    def _note_edit(self, nid, var):
        from jarvis.pc import notes
        if var.get().strip():
            notes.update_note(nid, var.get())
            self._say("Note updated.")

    def _note_delete(self, nid):
        from jarvis.pc import notes
        notes.delete_note(nid)
        self._fill_notes()

    # ---- routines
    def _routines(self, tab):
        ctk = self.ctk
        self.routines_frame = ctk.CTkScrollableFrame(tab, height=240)
        self.routines_frame.pack(fill="both", expand=True)
        self.new_steps = []
        box = ctk.CTkFrame(tab)
        box.pack(fill="x", pady=4)
        ctk.CTkLabel(box, text="New routine", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=6)
        self.rname = ctk.CTkEntry(box, placeholder_text="Name, for example gaming mode")
        self.rname.pack(fill="x", padx=6, pady=2)
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=6)
        names = self.h.call("tool_names", default=None) or []
        self.rtool = ctk.CTkComboBox(row, values=names or [""], width=200)
        self.rtool.set(names[0] if names else "")
        self.rtool.pack(side="left")
        self.rargs = ctk.CTkEntry(row, placeholder_text="options, for example level=40")
        self.rargs.pack(side="left", fill="x", expand=True, padx=4)
        ctk.CTkButton(row, text="Add step", width=80, command=self._add_step).pack(side="left")
        self.rsteps = ctk.CTkLabel(box, text="No steps yet.", anchor="w", justify="left", wraplength=620)
        self.rsteps.pack(fill="x", padx=6)
        ctk.CTkButton(box, text="Save routine", width=120, command=self._save_routine).pack(anchor="w", padx=6, pady=4)
        self._fill_routines()

    def _fill_routines(self):
        ctk = self.ctk
        for w in self.routines_frame.winfo_children():
            w.destroy()
        try:
            from jarvis.pc import routines
            data = routines.load_routines()
        except Exception:
            ctk.CTkLabel(self.routines_frame, text="Routines are not available.").pack(anchor="w")
            return
        if not data:
            ctk.CTkLabel(self.routines_frame, text="No routines yet.").pack(anchor="w")
        for name, r in data.items():
            row = ctk.CTkFrame(self.routines_frame)
            row.pack(fill="x", pady=2)
            steps = "; ".join(s["tool"].replace("_", " ") + (f" {s['args']}" if s.get("args") else "")
                              for s in r.get("steps", []))
            ctk.CTkLabel(row, text=f"{name}: {steps}", anchor="w", justify="left",
                         wraplength=420).pack(side="left", padx=4)
            v = ctk.BooleanVar(value=bool(r.get("skip_confirm")))
            ctk.CTkCheckBox(row, text="don't ask", variable=v, width=90,
                            command=lambda n=name, v=v: self._skip(n, v)).pack(side="right", padx=4)
            ctk.CTkButton(row, text="Delete", width=56, fg_color="firebrick",
                          command=lambda n=name: self._routine_delete(n)).pack(side="right")

    def _skip(self, name, var):
        from jarvis.pc import routines
        data = routines.load_routines()
        if name in data:
            data[name]["skip_confirm"] = bool(var.get())
            routines.save_routines(data)

    def _routine_delete(self, name):
        from jarvis.pc import routines
        data = routines.load_routines()
        data.pop(name, None)
        routines.save_routines(data)
        self._fill_routines()

    def _add_step(self):
        tool = self.rtool.get().strip()
        args, err = parse_args_text(self.rargs.get())
        if not tool:
            return self._say("Pick a tool for the step.")
        if err:
            return self._say(err)
        self.new_steps.append({"tool": tool, "args": args})
        self.rsteps.configure(text="Steps: " + "; ".join(
            f"{i}. {s['tool']} {s['args'] or ''}".strip() for i, s in enumerate(self.new_steps, 1)))
        self.rargs.delete(0, "end")

    def _save_routine(self):
        name = self.rname.get().strip()
        if not name or not self.new_steps:
            return self._say("A routine needs a name and at least one step.")
        from jarvis.pc import routines
        data = routines.load_routines()
        old = routines.find_name(data, name)
        skip = data[old].get("skip_confirm", False) if old else False
        data[old or name] = {"steps": list(self.new_steps), "skip_confirm": skip}
        routines.save_routines(data)
        self.new_steps = []
        self.rsteps.configure(text="No steps yet.")
        self.rname.delete(0, "end")
        self._fill_routines()
        self._say("Routine saved.")

    def _minecraft(self, tab):
        ctk = self.ctk
        self._label(tab, "Coming in Part B", bold=True)
        for text in ("Minecraft mode", "Waypoints"):
            ctk.CTkSwitch(tab, text=text, state="disabled").pack(anchor="w", padx=8, pady=4)
        self._label(tab, "Minecraft folder (override)")
        ctk.CTkEntry(tab, state="disabled", width=300, placeholder_text="Coming in Part B").pack(anchor="w", padx=8)

    def _about(self, tab):
        try:
            from jarvis import __version__ as ver
        except Exception:
            ver = "dev"
        self._label(tab, f"Jarvis version {ver}", bold=True)
        self.log_dir = str(paths.data_dir() / "logs")
        self._label(tab, f"Log files: {self.log_dir}")
        self.ctk.CTkButton(tab, text="Open logs folder", command=self._open_logs).pack(anchor="w", padx=8, pady=6)

    # ---- actions
    def _say(self, text):
        self.status.configure(text=text)

    def _open_logs(self):
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            os.startfile(self.log_dir)
        except Exception:
            log.exception("could not open logs folder")

    def _test_key(self):
        key = secrets.clean_key(self.key_entry.get()) or secrets.get_api_key() or ""
        if not key:
            return self._say("Paste a key first.")
        ok, msg = self.h.call("test_key", key, default=(False, "Could not test the key."))
        self._say(("Key works. " if ok else "Key problem: ") + str(msg))

    def _test_wake(self):
        self.wake_label.configure(text='Say "Hey Jarvis" now (10 seconds)...')
        self.h.call("test_wake")

    def wake_result(self, text):
        """The app calls this (from any thread) with the outcome of the wake word test."""
        try:
            self.win.after(0, lambda: self.wake_label.configure(text=text))
        except Exception:
            pass

    def _clear_history(self):
        self.h.call("clear_history")
        self._say("History cleared.")

    def _tab_changed(self):
        self._on_voice_tab = self.tabs.get() == "Voice"
        if self._on_voice_tab and self._meter_job is None:
            self._poll_meter()

    def _poll_meter(self):
        if not self._on_voice_tab:
            self._meter_job = None
            return
        lvl = self.h.call("mic_level", default=0.0) or 0.0
        try:
            self.meter.set(max(0.0, min(1.0, float(lvl))))
            self._meter_job = self.win.after(100, self._poll_meter)
        except Exception:
            self._meter_job = None

    def collect_form(self) -> dict:
        labelled = {"personality": PERSONALITY_LABELS, "reply_mode": REPLY_LABELS,
                    "history_mode": HISTORY_LABELS, "startup_greeting": GREETING_LABELS,
                    "theme": THEME_LABELS, "auto_look": AUTO_LOOK_LABELS}
        form = {}
        for k, v in self.vars.items():
            val = v.get()
            if k in labelled:
                val = label_to_key(labelled[k], val)
            elif k in ("mic_device", "speaker_device") and val == DEFAULT_DEVICE:
                val = ""
            form[k] = val
        form["daily_limits"] = {m: v.get() for m, v in self.limit_vars.items() if v.get().strip()}
        form["app_aliases"] = self.aliases.values()
        form["game_list"] = parse_list(self.games_box.get("1.0", "end"))
        form["never_screenshot"] = parse_list(self.never_box.get("1.0", "end"))
        return form

    def save(self):
        cfg, errors = build_cfg(self.cfg, self.collect_form())
        if errors:
            return self._say(errors[0])
        key = secrets.clean_key(self.key_entry.get())
        if key:
            if not secrets.looks_like_key(key):
                return self._say("That doesn't look like a Gemini API key.")
            secrets.set_api_key(key)
            self.key_entry.delete(0, "end")
            self.key_status.configure(text=mask_key(key))
        mic_changed = cfg["mic_device"] != self.cfg.get("mic_device")
        config.save(cfg)
        self.cfg = cfg
        self.h.call("apply", cfg)
        if mic_changed:
            self.h.call("reopen_mic")
        self.result = cfg
        self._say("Saved.")

    def close(self):
        self._on_voice_tab = False
        try:
            self.win.destroy()
        except Exception:
            pass

    def show(self):
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
