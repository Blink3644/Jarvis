"""Settings window (Stage A4): tabs for General, Voice, PC, Notes, Routines, Integrations, Minecraft, About.

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
import threading

from .. import config, paths, providers, secrets

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
                  "remember": "Remember recent conversations (sends more data to your AI services)"}
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
                       ("stt_model", config.STT_MODELS),
                       ("startup_greeting", tuple(GREETING_LABELS)), ("theme", tuple(THEME_LABELS))):
        if k in f:
            if f[k] not in allowed:
                errors.append(f"Unknown choice for {k}.")
            else:
                cfg[k] = f[k]
    for k in ("clickup_team_id", "clickup_list_id", "clickup_list_name", "spotify_client_id"):
        if k in f:
            cfg[k] = str(f[k] or "").strip()
    if "live_model" in f:
        cfg["live_model"] = str(f["live_model"] or "").strip() or "gemini-3.8-live"
    if "live_idle_seconds" in f:
        v = _num(f["live_idle_seconds"], "Live mode idle seconds", 5, 300, float, errors)
        if v is not None:
            cfg["live_idle_seconds"] = v
    for k in ("wake_enabled", "ptt_enabled", "stop_word_enabled", "start_with_windows", "live_mode",
              "calendar_enabled", "clickup_enabled"):
        if k in f:
            cfg[k] = bool(f[k])
    if "wake_model" in f:
        wm = str(f["wake_model"] or "").strip() or "hey_jarvis"
        if wm != "hey_jarvis" and (not wm.lower().endswith(".onnx") or "/" in wm or "\\" in wm):
            errors.append("Unknown wake word choice.")
        else:
            cfg["wake_model"] = wm
    if "wake_sensitivity" in f:
        v = _num(f["wake_sensitivity"], "Wake word sensitivity", 0.1, 0.9, float, errors)
        if v is not None:
            cfg["wake_sensitivity"] = round(v, 2)
    if "obs_port" in f:
        v = _num(f["obs_port"], "OBS port", 1, 65535, int, errors)
        if v is not None:
            cfg["obs_port"] = v
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
    # picking a virtual input (shown only after ticking "show virtual inputs") unlocks it by name
    virtual = f.get("virtual_inputs") or ()
    if cfg.get("mic_device") in virtual:
        allowed = list(cfg.get("allowed_virtual_mics") or [])
        if cfg["mic_device"] not in allowed:
            allowed.append(cfg["mic_device"])
        cfg["allowed_virtual_mics"] = allowed
    return cfg, errors


def move_item(items: list, i: int, d: int) -> list:
    """Copy of items with item i moved up (d=-1) or down (d=1); out-of-range moves do nothing."""
    out = list(items)
    j = i + d
    if 0 <= i < len(out) and 0 <= j < len(out):
        out[i], out[j] = out[j], out[i]
    return out


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
        for name in ("General", "AI providers", "Voice", "PC", "Notes", "Routines", "Integrations", "Minecraft", "About"):
            self.tabs.add(name)
        self._general(self.tabs.tab("General"))
        self._providers_tab(self.tabs.tab("AI providers"))
        self._voice(self.tabs.tab("Voice"))
        self._pc(self.tabs.tab("PC"))
        self._notes(self.tabs.tab("Notes"))
        self._routines(self.tabs.tab("Routines"))
        self._integrations(self.tabs.tab("Integrations"))
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
        self._label(p, "Privacy: your questions, relevant notes (never secret ones), Calendar/ClickUp details, "
                       "and screenshots or PC audio when you ask, are sent to the AI services you add here "
                       "(Google, OpenAI, Anthropic, etc.). Free tiers may use this data to improve their products.",
                    color="orange")
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

    # ---------- AI providers tab ----------
    def _providers_tab(self, tab):
        ctk = self.ctk
        self.prov_list = copy.deepcopy(self.cfg.get("providers") or [dict(providers.GEMINI_ENTRY)])
        self.prov_keys: dict[str, str] = {}
        self.prov_rows: list[dict] = []
        self._label(tab, "Jarvis asks the top provider first. When it runs out or fails, it moves down the "
                         "list, tells you once, and goes back up when the top one works again. Keys are kept "
                         "in Windows Credential Manager. The Gemini key is on the General tab.", wrap=640)
        add = ctk.CTkFrame(tab, fg_color="transparent")
        add.pack(fill="x", padx=8, pady=4)
        names = {p["name"]: k for k, p in providers.PRESETS.items()}
        self.prov_add_var = ctk.StringVar(value=next(iter(names)))
        ctk.CTkOptionMenu(add, variable=self.prov_add_var, values=list(names)).pack(side="left")
        ctk.CTkButton(add, text="Add provider", width=110,
                      command=lambda: self._prov_add(names[self.prov_add_var.get()])).pack(side="left", padx=6)
        self.prov_frame = ctk.CTkScrollableFrame(tab)
        self.prov_frame.pack(fill="both", expand=True, padx=4, pady=4)
        self._prov_draw()

    def _prov_sync(self):
        """Copy what's typed in the rows back into prov_list / prov_keys."""
        for row in self.prov_rows:
            e = row["entry"]
            e["enabled"] = bool(row["enabled"].get())
            if "models" in row:
                e["models"] = [m.strip() for m in row["models"].get().split(",") if m.strip()]
            if "base_url" in row:
                e["base_url"] = row["base_url"].get().strip()
            if "key" in row:
                k = secrets.clean_key(row["key"].get())
                if k:
                    self.prov_keys[e["id"]] = k

    def _prov_draw(self):
        ctk = self.ctk
        for w in self.prov_frame.winfo_children():
            w.destroy()
        self.prov_rows = []
        for i, e in enumerate(self.prov_list):
            box = ctk.CTkFrame(self.prov_frame)
            box.pack(fill="x", pady=4, padx=2)
            top = ctk.CTkFrame(box, fg_color="transparent")
            top.pack(fill="x", padx=6, pady=(4, 0))
            row = {"entry": e, "enabled": ctk.BooleanVar(value=e.get("enabled", True))}
            ctk.CTkCheckBox(top, text=f"{i + 1}. {e['name']}", variable=row["enabled"]).pack(side="left")
            ctk.CTkButton(top, text="Up", width=44, command=lambda i=i: self._prov_move(i, -1)).pack(side="left", padx=2)
            ctk.CTkButton(top, text="Down", width=54, command=lambda i=i: self._prov_move(i, 1)).pack(side="left", padx=2)
            if e.get("kind") != "gemini":
                ctk.CTkButton(top, text="Remove", width=70, command=lambda i=i: self._prov_remove(i)).pack(side="left", padx=2)
                preset = providers.PRESETS.get(e.get("preset"), {})
                caps = [c for c, on in (("pictures", e.get("images")), ("audio", e.get("audio"))) if on]
                ctk.CTkLabel(top, text="can take: " + (", ".join(caps) or "text only")).pack(side="left", padx=8)
                if not preset.get("no_key"):
                    have = secrets.get_provider_key(e["id"])
                    r = ctk.CTkFrame(box, fg_color="transparent")
                    r.pack(fill="x", padx=6)
                    row["key"] = ctk.CTkEntry(r, show="*", width=260, placeholder_text=(
                        "Key saved. Paste a new one to change it" if have else "Paste your API key here"))
                    row["key"].pack(side="left")
                    ctk.CTkButton(r, text="Test key", width=80,
                                  command=lambda row=row: self._prov_test(row)).pack(side="left", padx=6)
                    if preset.get("key_url"):
                        ctk.CTkLabel(r, text=f"Get a key: {preset['key_url']}").pack(side="left")
                else:
                    ctk.CTkButton(box, text="Test connection", width=120,
                                  command=lambda row=row: self._prov_test(row)).pack(anchor="w", padx=6)
                if e.get("preset") == "custom" or not e.get("base_url"):
                    row["base_url"] = ctk.StringVar(value=e.get("base_url", ""))
                    ctk.CTkLabel(box, text="Web address (base URL), e.g. https://example.com/v1").pack(anchor="w", padx=6)
                    ctk.CTkEntry(box, textvariable=row["base_url"], width=360).pack(anchor="w", padx=6)
                row["models"] = ctk.StringVar(value=", ".join(e.get("models") or []))
                ctk.CTkLabel(box, text="Model(s), best first, separated by commas. Test key lists the names.").pack(anchor="w", padx=6)
                ctk.CTkEntry(box, textvariable=row["models"], width=420).pack(anchor="w", padx=6, pady=(0, 4))
                row["status"] = ctk.CTkLabel(box, text="", wraplength=600, justify="left")
                row["status"].pack(anchor="w", padx=6)
            else:
                ctk.CTkLabel(top, text="models and key: General tab").pack(side="left", padx=8)
            self.prov_rows.append(row)

    def _prov_add(self, preset):
        self._prov_sync()
        self.prov_list.append(providers.new_entry(preset, {e["id"] for e in self.prov_list}))
        self._prov_draw()

    def _prov_move(self, i, d):
        self._prov_sync()
        self.prov_list = move_item(self.prov_list, i, d)
        self._prov_draw()

    def _prov_remove(self, i):
        self._prov_sync()
        self.prov_list.pop(i)
        self._prov_draw()

    def _prov_test(self, row):
        import threading
        self._prov_sync()
        e = dict(row["entry"])
        key = self.prov_keys.get(e["id"]) or secrets.get_provider_key(e["id"]) or ""
        if not key and not providers.PRESETS.get(e.get("preset"), {}).get("no_key"):
            return row["status"].configure(text="Paste a key first.")
        row["status"].configure(text="Testing...")

        def work():
            try:
                models = providers.make_adapter(e).list_models(key)
                msg = f"Key works. {len(models)} models available: " + ", ".join(models[:40])
            except providers.ProviderError as ex:
                msg = "Key problem: " + ("the key was refused." if ex.code in (401, 403) else str(ex)[:200])
            except Exception as ex:
                msg = f"Couldn't reach {e['name']}: {str(ex)[:150]}"
            try:
                self.win.after(0, lambda: row["status"].configure(text=msg))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _fill_usage(self):
        ctk = self.ctk
        for w in self.usage_frame.winfo_children():
            w.destroy()
        used = {m: u for m, u, _ in (self.h.call("check_usage", default=None) or [])}
        self.limit_vars = {}
        limits = self.cfg.get("daily_limits", {})
        for model in dict.fromkeys([self.cfg["model_main"], self.cfg["model_fallback"], *used, *limits]):
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
            ins = [d["name"] for d in devices.list_inputs(c.get("allowed_virtual_mics", ()))]
            self.virtual_inputs = [d["name"] for d in devices.list_virtual_inputs() if d["name"] not in ins]
            outs = [d["name"] for d in devices.list_outputs()]
        except Exception:
            log.exception("could not list audio devices")
            ins, outs, self.virtual_inputs = [], [], []
        self._label(p, "Microphone. Jarvis only listens to this microphone.", bold=True)
        v = self._var("mic_device", c["mic_device"] or DEFAULT_DEVICE)
        mic_menu = ctk.CTkOptionMenu(p, variable=v, values=[DEFAULT_DEVICE, *ins])
        mic_menu.pack(anchor="w", padx=8)
        if self.virtual_inputs:
            show = ctk.BooleanVar(value=False)

            def toggle():
                extra = self.virtual_inputs if show.get() else []
                mic_menu.configure(values=[DEFAULT_DEVICE, *ins, *extra])
            ctk.CTkCheckBox(p, text="Show virtual inputs (advanced)", variable=show,
                            command=toggle).pack(anchor="w", padx=8, pady=(4, 0))
            self._label(p, "Only pick a virtual input (like VB-Cable) if it carries just your own voice. "
                           "If it also carries game, music or Discord sound, Jarvis may wake itself up.",
                        color="gray")
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
        self._check(p, "wake_enabled", c["wake_enabled"], "Wake word on")
        self._wake_picker(p)
        self._label(p, "Wake word sensitivity (low = strict, high = picks up more)")
        v = self._var("wake_sensitivity", float(c["wake_sensitivity"]), "float")
        ctk.CTkSlider(p, from_=0.1, to=0.9, variable=v, width=300).pack(anchor="w", padx=8)
        self._check(p, "ptt_enabled", c["ptt_enabled"], "Push-to-talk (hold a key to speak)")
        self._entry(p, "ptt_key", c["ptt_key"], "Push-to-talk key (for example ctrl_r, alt_r, f8)", width=150)
        self._entry(p, "followup_seconds", c["followup_seconds"],
                    "Keep listening after a reply for this many seconds (0 = off)", width=80)
        self._check(p, "stop_word_enabled", c["stop_word_enabled"], 'Saying "stop" interrupts Jarvis')
        self._label(p, "Live voice mode (experimental)", bold=True)
        self._check(p, "live_mode", c.get("live_mode", False),
                    "Live voice mode: talk with Gemini in real time (Google Gemini key only; "
                    "if it fails, Jarvis uses normal mode). Headphones work best.")
        self._entry(p, "live_model", c.get("live_model", "gemini-3.8-live"), "Live model name", width=220)
        self._entry(p, "live_idle_seconds", c.get("live_idle_seconds", 20),
                    "End the Live conversation after this many quiet seconds (saves quota)", width=80)
        self._label(p, "Voice (pick one, or type any edge-tts voice name)")
        v = self._var("tts_voice", c["tts_voice"])
        ctk.CTkComboBox(p, variable=v, values=VOICES, width=300).pack(anchor="w", padx=8)
        self._label(p, "Speech recognition model (small.en is more accurate, ~1 s slower; "
                       "first use downloads ~480 MB; restart Jarvis after changing)")
        v = self._var("stt_model", c.get("stt_model", "base.en"))
        ctk.CTkOptionMenu(p, variable=v, values=list(config.STT_MODELS)).pack(anchor="w", padx=8)

    def _wake_picker(self, p):
        from ..voice import wakewords
        ctk = self.ctk
        self._label(p, "Wake word (click Save after changing it)")
        self.wake_choices = wakewords.choices()
        current = self.cfg.get("wake_model") or wakewords.DEFAULT
        if current not in self.wake_choices:
            current = wakewords.DEFAULT
        self.wake_var = self._var("wake_model", self.wake_choices[current])
        self.wake_menu = ctk.CTkOptionMenu(p, variable=self.wake_var,
                                           values=list(self.wake_choices.values()), width=300)
        self.wake_menu.pack(anchor="w", padx=8)
        ctk.CTkButton(p, text="Train a custom wake word (advanced)...", width=260,
                      command=self._train_guide).pack(anchor="w", padx=8, pady=(4, 0))

    def _refresh_wake_menu(self, select=None):
        from ..voice import wakewords
        self.wake_choices = wakewords.choices()
        self.wake_menu.configure(values=list(self.wake_choices.values()))
        if select in self.wake_choices:
            self.wake_var.set(self.wake_choices[select])
        elif self.wake_var.get() not in self.wake_choices.values():
            self.wake_var.set(self.wake_choices[wakewords.DEFAULT])

    def _train_guide(self):
        """A small window that walks through training on Google Colab and importing the result."""
        from ..voice import wakewords
        ctk = self.ctk
        top = ctk.CTkToplevel(self.win)
        top.title("Train a custom wake word")
        top.geometry("620x560")
        top.transient(self.win)
        box = ctk.CTkScrollableFrame(top)
        box.pack(fill="both", expand=True, padx=8, pady=8)
        self._label(box, "Train your own wake word", bold=True, wrap=560)
        for line in wakewords.TRAINING_STEPS:
            self._label(box, line, wrap=560)
        self._label(box, "Tip: this is optional. \"Hey Jarvis\" stays available and is used automatically "
                         "if a trained model ever fails to load.", wrap=560, color="gray")
        status = ctk.CTkLabel(box, text="", anchor="w", justify="left", wraplength=560)

        def open_page():
            import webbrowser
            webbrowser.open(wakewords.COLAB_URL)

        def do_import():
            from tkinter import filedialog
            path = filedialog.askopenfilename(
                parent=top, title="Pick your trained wake word (.onnx)",
                filetypes=[("Wake word model", "*.onnx"), ("All files", "*.*")],
                initialdir=os.path.join(os.path.expanduser("~"), "Downloads"))
            if not path:
                return
            name, msg = wakewords.import_model(path)
            status.configure(text=msg)
            if name:
                self._refresh_wake_menu(select=name)

        def do_remove():
            key = label_to_key(self.wake_choices, self.wake_var.get())
            if not key or key == wakewords.DEFAULT:
                return status.configure(text="Pick a trained wake word in the list first (Hey Jarvis can't be removed).")
            wakewords.remove_model(key)
            self._refresh_wake_menu(select=wakewords.DEFAULT)
            status.configure(text=f"Removed \"{wakewords.nice_name(key)}\". Click Save to switch back to Hey Jarvis.")

        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", pady=6)
        ctk.CTkButton(row, text="Open the training page", command=open_page).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Import a trained model...", command=do_import).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Remove selected", command=do_remove, fg_color="gray40").pack(side="left", padx=4)
        status.pack(fill="x", pady=4)

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
        self._label(p, 'Warning: "Always" sends whatever is on your screen to your AI service with every question.',
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
            ctk.CTkLabel(row, text=("SECRET (local only)  " if n.get("secret") else "")
                         + f'{n.get("scope", "global")}  {str(n.get("created", ""))[:10]}',
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

    # ---- Google Calendar and ClickUp (Stage A5)
    def _integrations(self, tab):
        ctk, c = self.ctk, self.cfg
        p = self._scroll(tab)
        self._label(p, "Optional. Leave these off if you don't use them. When they're on, Jarvis can read and "
                       "add events or tasks, and the 'Daily brief' greeting mentions today's.", wrap=640)
        self._label(p, "Google Calendar", bold=True)
        self._check(p, "calendar_enabled", c.get("calendar_enabled", False), "Use Google Calendar")
        self.gcal_status = self._label(p, "")
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=4)
        ctk.CTkButton(row, text="Setup guide", width=110, command=self._gcal_guide).pack(side="left")
        ctk.CTkButton(row, text="1. Choose credentials.json", width=190,
                      command=self._gcal_pick).pack(side="left", padx=6)
        ctk.CTkButton(row, text="2. Connect Google account", width=190,
                      command=self._gcal_connect).pack(side="left")
        ctk.CTkButton(p, text="Disconnect Google", width=150, fg_color="gray40",
                      command=self._gcal_disconnect).pack(anchor="w", padx=8, pady=2)
        self._gcal_refresh()

        self._label(p, "ClickUp", bold=True)
        self._check(p, "clickup_enabled", c.get("clickup_enabled", False), "Use ClickUp")
        self.cu_status = self._label(p, "")
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=4)
        self.cu_entry = ctk.CTkEntry(row, show="*", width=300,
                                     placeholder_text="Paste your ClickUp token (starts with pk_)")
        self.cu_entry.pack(side="left")
        ctk.CTkButton(row, text="Connect", width=90, command=self._cu_connect).pack(side="left", padx=6)
        ctk.CTkButton(row, text="Disconnect", width=90, fg_color="gray40",
                      command=self._cu_disconnect).pack(side="left")
        self._label(p, "Where to get the token: in ClickUp click your avatar (bottom left) > Settings > "
                       "Apps > API Token > Generate, then Copy.", color="gray60")
        self._label(p, "List for new tasks")
        self.cu_lists = {}                      # label -> list dict
        self.cu_team = c.get("clickup_team_id", "")
        cur = c.get("clickup_list_name") or "(connect first)"
        self.cu_list_var = ctk.StringVar(value=cur)
        self.cu_menu = ctk.CTkOptionMenu(p, variable=self.cu_list_var, values=[cur], width=300)
        self.cu_menu.pack(anchor="w", padx=8)
        self._cu_refresh()

        self._spotify_section(p, c)

        self._label(p, "OBS (\"Jarvis, clip that\")", bold=True)
        self._label(p, "Saves the last stretch of gameplay as a video using OBS's Replay Buffer. In OBS: "
                       "Settings > Output > Replay Buffer > tick 'Enable Replay Buffer'; then Tools > WebSocket "
                       "Server Settings > tick 'Enable WebSocket server' and click 'Show Connect Info' for the "
                       "port and password.", color="gray60", wrap=640)
        self._entry(p, "obs_port", c.get("obs_port", 4455), "OBS WebSocket port", width=90)
        self.obs_status = self._label(p, "")
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=4)
        self.obs_entry = ctk.CTkEntry(row, show="*", width=300,
                                      placeholder_text="OBS WebSocket password (leave empty if none)")
        self.obs_entry.pack(side="left")
        ctk.CTkButton(row, text="Save password", width=110, command=self._obs_save).pack(side="left", padx=6)
        ctk.CTkButton(row, text="Test OBS", width=90, command=self._obs_test).pack(side="left")
        self._obs_refresh()

    def _obs_refresh(self):
        from jarvis.pc import obs_clip
        self.obs_status.configure(text="Password saved." if obs_clip.get_password() else "No password saved.")

    def _obs_save(self):
        from jarvis.pc import obs_clip
        pw = self.obs_entry.get().strip()
        if pw:
            obs_clip.set_password(pw)
        else:
            obs_clip.clear_password()
        self.obs_entry.delete(0, "end")
        self._obs_refresh()
        self._say("OBS password saved." if pw else "OBS password removed.")

    def _obs_test(self):
        from jarvis.pc import obs_clip
        try:
            port = int(str(self.vars["obs_port"].get()).strip())
        except ValueError:
            return self._say("The OBS port must be a number (usually 4455).")
        self._say("Checking OBS...")

        def work():
            try:
                with obs_clip.ObsClient(port, obs_clip.get_password()) as obs:
                    on = obs.request("GetReplayBufferStatus").get("outputActive")
                msg = ("OBS is connected and the Replay Buffer is running. 'Clip that' is ready." if on else
                       "OBS is connected, but the Replay Buffer is off. Click 'Start Replay Buffer' in OBS.")
            except obs_clip.ObsError as e:
                msg = str(e)
            except Exception:
                log.exception("OBS test failed")
                msg = "Couldn't test OBS. Details are in the log."
            self._later(lambda: self._say(msg))
        threading.Thread(target=work, daemon=True).start()

    def _later(self, fn):
        try:
            self.win.after(0, fn)
        except Exception:
            pass

    def _gcal_refresh(self):
        from jarvis.pc import google_calendar as g
        if g.is_connected():
            t = "Connected."
        elif g.has_credentials():
            t = "credentials.json is in place. Next: Connect Google account."
        else:
            t = "Not set up. Open the Setup guide (about 10 minutes, once)."
        self.gcal_status.configure(text=t)

    def _gcal_guide(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        try:
            os.startfile(os.path.join(root, "GOOGLE_CALENDAR_SETUP.md"))
        except Exception:
            log.exception("could not open the calendar guide")
            self._say("Open GOOGLE_CALENDAR_SETUP.md in the Jarvis folder.")

    def _gcal_pick(self):
        from tkinter import filedialog
        from jarvis.pc import google_calendar as g
        path = filedialog.askopenfilename(parent=self.win, title="Choose credentials.json",
                                          filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        err = g.import_credentials(path)
        self._say(err or "credentials.json saved. Now click Connect Google account.")
        self._gcal_refresh()

    def _gcal_connect(self):
        from jarvis.pc import google_calendar as g
        if not g.has_credentials():
            return self._say("Choose your credentials.json first.")
        self._say("Your browser will open. Sign in to Google and click Allow (you have 5 minutes).")

        def work():
            err = g.connect()

            def done():
                if not err:
                    self.vars["calendar_enabled"].set(True)
                self._say(err or "Google Calendar connected. Click Save.")
                self._gcal_refresh()
            self._later(done)
        threading.Thread(target=work, daemon=True).start()

    def _gcal_disconnect(self):
        from jarvis.pc import google_calendar as g
        g.disconnect()
        self.vars["calendar_enabled"].set(False)
        self._gcal_refresh()
        self._say("Google Calendar disconnected. Click Save.")

    def _cu_refresh(self):
        from jarvis.pc import clickup as cu
        self.cu_status.configure(text="Connected." if cu.get_token() and self.cu_team else "Not set up.")

    def _cu_connect(self):
        from jarvis.pc import clickup as cu
        token = secrets.clean_key(self.cu_entry.get()) or cu.get_token() or ""
        if not cu.looks_like_token(token):
            return self._say("Paste your ClickUp token first (it starts with pk_).")
        self._say("Checking the token with ClickUp...")

        def work():
            try:
                client = cu.Client(token)
                teams = client.teams()
                if not teams:
                    raise cu.ClickUpError("That token has no ClickUp workspace.")
                team = teams[0]
                lists = client.lists(team["id"])
                err = ""
            except cu.ClickUpError as e:
                err, team, lists = str(e), None, []
            except Exception:
                log.exception("ClickUp connect failed")
                err, team, lists = "Couldn't connect to ClickUp. Details are in the log.", None, []
            self._later(lambda: self._cu_connected(token, err, team, lists))
        threading.Thread(target=work, daemon=True).start()

    def _cu_connected(self, token, err, team, lists):
        from jarvis.pc import clickup as cu
        if err:
            return self._say(err)
        cu.set_token(token)
        self.cu_entry.delete(0, "end")
        self.cu_team = team["id"]
        self.cu_lists = {f"{li['name']}  ({li['where']})": li for li in lists}
        labels = list(self.cu_lists) or ["(no lists found)"]
        self.cu_menu.configure(values=labels)
        keep = next((lab for lab, li in self.cu_lists.items()
                     if li["id"] == self.cfg.get("clickup_list_id")), labels[0])
        self.cu_list_var.set(keep)
        self.vars["clickup_enabled"].set(True)
        self._cu_refresh()
        self._say(f"Connected to the {team['name']} workspace. Pick a list, then click Save.")

    def _cu_disconnect(self):
        from jarvis.pc import clickup as cu
        cu.disconnect()
        self.cu_team = ""
        self.cu_lists = {}
        self.vars["clickup_enabled"].set(False)
        self._cu_refresh()
        self._say("ClickUp disconnected. Click Save.")

    def _spotify_section(self, p, c):
        from jarvis.pc import spotify as sp
        self._label(p, "Spotify (Premium)", bold=True)
        self.sp_status = self._label(p, "")
        self._entry(p, "spotify_client_id", c.get("spotify_client_id", ""),
                    "Your own Spotify Client ID (optional; leave empty to use the built-in one)")
        row = self.ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=4)
        self.ctk.CTkButton(row, text="Connect Spotify", width=130, command=self._sp_connect).pack(side="left")
        self.ctk.CTkButton(row, text="Disconnect", width=90, fg_color="gray40",
                           command=self._sp_disconnect).pack(side="left", padx=6)
        self._label(p, "Connect opens your browser once to log in to Spotify. Using your own Client ID: at "
                       "developer.spotify.com > Dashboard > Create app, tick 'Web API', and add this Redirect "
                       f"URI: {sp.REDIRECT_URI}", color="gray60", wrap=640)
        self._sp_refresh()

    def _sp_refresh(self):
        from jarvis.pc import spotify as sp
        if sp.get_refresh_token():
            txt = "Connected."
        elif not sp.client_id({"spotify_client_id": self.vars["spotify_client_id"].get()}):
            txt = "Not set up. Paste a Client ID, then click Connect Spotify."
        else:
            txt = "Not connected."
        self.sp_status.configure(text=txt)

    def _sp_connect(self):
        from jarvis.pc import spotify as sp
        cid = sp.client_id({"spotify_client_id": self.vars["spotify_client_id"].get()})
        if not cid:
            return self._say("Paste a Spotify Client ID first.")
        self._say("Log in to Spotify in the browser window that just opened...")

        def work():
            try:
                msg = sp.connect(cid) + " Click Save."
            except sp.SpotifyError as e:
                msg = str(e)
            except Exception:
                log.exception("Spotify connect failed")
                msg = "Couldn't connect to Spotify. Details are in the log."
            self._later(lambda: (self._say(msg), self._sp_refresh()))
        threading.Thread(target=work, daemon=True).start()

    def _sp_disconnect(self):
        from jarvis.pc import spotify as sp
        sp.disconnect()
        self._sp_refresh()
        self._say("Spotify disconnected.")

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
        self.wake_label.configure(text="Say your wake word now (10 seconds)...")
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
                    "theme": THEME_LABELS, "auto_look": AUTO_LOOK_LABELS,
                    "wake_model": getattr(self, "wake_choices", {})}
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
        form["virtual_inputs"] = getattr(self, "virtual_inputs", [])
        if hasattr(self, "cu_list_var"):
            form["clickup_team_id"] = self.cu_team
            li = self.cu_lists.get(self.cu_list_var.get())
            if li:
                form["clickup_list_id"], form["clickup_list_name"] = li["id"], li["name"]
            elif not self.cu_team:
                form["clickup_list_id"] = form["clickup_list_name"] = ""
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
        if hasattr(self, "prov_list"):
            self._prov_sync()
            for e in self.prov_list:
                if e.get("kind") != "gemini" and e.get("enabled") and not e.get("models"):
                    return self._say(f"Type at least one model name for {e['name']} (Test key lists them).")
            kept = {e["id"] for e in self.prov_list}
            for e in self.cfg.get("providers") or []:
                if e["id"] not in kept and e.get("kind") != "gemini":
                    secrets.delete_provider_key(e["id"])     # removed provider: forget its key
            cfg["providers"] = copy.deepcopy(self.prov_list)
            for pid, k in self.prov_keys.items():
                secrets.set_provider_key(pid, k)
            for row in self.prov_rows:
                if "key" in row:
                    row["key"].delete(0, "end")
            self.prov_keys.clear()
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
