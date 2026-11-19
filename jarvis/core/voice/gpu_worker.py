"""Graphics-card speech worker: Whisper large-v3-turbo (ONNX fp16) on DirectML.

Runs in its own small venv (onnxruntime-directml + numpy only), because faster-whisper's
`onnxruntime` dependency and `onnxruntime-directml` install the same module and can't share
Jarvis's venv. Jarvis computes the log-mel features and turns token ids back into text; this
process only runs the encoder and a greedy decoder.

Protocol (stdin/stdout, binary): request = 4-byte length + JSON header {"prompt": [ids], "n": frames}
followed by n*128 float32 features; reply = one JSON line {"ids": [...], "no_speech": p, "logprob": avg}.
The first reply line after start is {"ready": true, "providers": [...]} or {"error": "..."}.
Uses only the standard library, numpy and onnxruntime, so it never imports Jarvis code.
"""
import json
import struct
import sys

EOT, SOT, EN, TRANSCRIBE, NO_SPEECH, NO_TIMESTAMPS = 50257, 50258, 50259, 50360, 50363, 50364
MAX_TOKENS = 120


def _send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _softmax_logp(x, i):
    import numpy as np
    m = float(np.max(x))
    return float(x[i] - m - np.log(np.sum(np.exp(x - m))))


def main(model_dir):
    import numpy as np
    import onnxruntime as ort
    if "DmlExecutionProvider" not in ort.get_available_providers():
        _send({"error": "DirectML is not available on this PC"})
        return
    so = ort.SessionOptions()
    # ORT_ENABLE_ALL crashes on these fp16 files (SimplifiedLayerNormFusion), BASIC is fine.
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    so.log_severity_level = 3

    def load(name):
        return ort.InferenceSession(f"{model_dir}/{name}.onnx", so, providers=["DmlExecutionProvider"])
    # The split decoders: the "merged" decoder's If-node gives garbage on DirectML.
    enc, dec0, decp = load("encoder_model_fp16"), load("decoder_model_fp16"), load("decoder_with_past_model_fp16")
    pnames = {i.name for i in decp.get_inputs()}
    _send({"ready": True, "providers": enc.get_providers()})
    inp = sys.stdin.buffer
    while True:
        head = inp.read(4)
        if len(head) < 4:
            return
        req = json.loads(inp.read(struct.unpack("<I", head)[0]))
        n = req["n"]
        feats = np.frombuffer(inp.read(n * 128 * 4), np.float32).reshape(1, 128, n)
        hs = enc.run(None, {"input_features": feats})[0]
        prefix = list(req.get("prompt") or []) + [SOT, EN, TRANSCRIBE, NO_TIMESTAMPS]
        sot_at = len(prefix) - 4
        outs = dec0.run(None, {"input_ids": np.array([prefix], np.int64), "encoder_hidden_states": hs})
        names = [o.name for o in dec0.get_outputs()]
        no_speech = float(np.exp(_softmax_logp(outs[0][0, sot_at], NO_SPEECH)))
        ids, lp, enc_kv = [], 0.0, {}
        for step in range(MAX_TOKENS):
            logits = outs[0][0, -1][:SOT]          # text tokens + EOT only: no timestamps/specials
            if step == 0:
                logits = logits.copy()
                logits[[220, EOT]] = -np.inf       # Whisper's begin_suppress_tokens
            nxt = int(np.argmax(logits))
            lp += _softmax_logp(logits, nxt)
            if nxt == EOT:
                break
            ids.append(nxt)
            past = {k.replace("present", "past_key_values"): v for k, v in zip(names[1:], outs[1:])}
            if step == 0:
                enc_kv = {k: v for k, v in past.items() if ".encoder." in k}
            feed = {k: v for k, v in past.items() if ".decoder." in k}
            feed.update(enc_kv)
            feed["input_ids"] = np.array([[nxt]], np.int64)
            if "encoder_hidden_states" in pnames:
                feed["encoder_hidden_states"] = hs
            outs = decp.run(None, {k: v for k, v in feed.items() if k in pnames})
            names = [o.name for o in decp.get_outputs()]
        _send({"ids": ids, "no_speech": no_speech, "logprob": lp / max(1, len(ids) + 1)})


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Exception as e:  # the parent logs this and falls back to the CPU engine
        _send({"error": f"{type(e).__name__}: {e}"})
