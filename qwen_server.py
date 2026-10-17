"""
Qwen3-TTS on PyTorch, as a small streaming server in an environment of its own.

Why this exists. Qwen-TTS Studio -- the way this tool first ran Qwen -- is a
GGML build whose DLLs are not signed, and Windows 11's Smart App Control, on by
default on a clean install, refuses every one of them. Nobody should have to
turn a security feature off to hear a voice, and Smart App Control cannot be
turned back on without reinstalling Windows. So the same model runs here on
PyTorch and Qwen's own qwen-tts package instead: the files involved are
python.org's signed Python and PyPI wheels Microsoft's cloud already knows, and
on 2026-10-17 not one of them was refused with Smart App Control enforcing.

It runs out of its own environment, started by qwen_torch_engine.py, for the
reason Breeze does: CUDA torch, transformers and qwen-tts are three gigabytes
this tool's own Python has no business growing.

Two things make it fast enough to speak live, and both were measured on an RTX
5080 laptop with the 1.7B Base model:

  * SDPA attention. qwen-tts falls back to eager attention when flash-attn is
    missing (it always is on Windows): 0.31x realtime. SDPA: 0.6x.
  * The code predictor as one CUDA graph. For every frame of sound -- twelve a
    second -- the talker asks a five-layer predictor for fifteen more codes
    through transformers' generate(), and that machinery cost ~90 ms a frame
    against 83 ms of sound. Its whole fifteen-token run has fixed shapes, so it
    is captured once and replayed: 12 ms a frame, and 1.6-1.7x realtime
    overall. No compiler, no Triton, no file built on the spot -- only torch's
    own CUDA graph API -- so there is nothing new on disk for Windows to judge.

And it streams. Each frame's codes are taken as the talker makes them and
decoded in short pieces with left context, the scheme qwen-tts's own
chunked_decode uses with longer ones, so the first sound leaves after a few
frames rather than after the whole line.

    python qwen_server.py --model <folder> --port <n>

POST /speak  {"text", "language", "embedding" | "clip" + "clip_text", "seed"}
             -> 24 kHz mono signed 16-bit little-endian PCM, streamed, until
                the line ends or the caller hangs up (which stops generation).
GET /health  -> 200 once the model is loaded and warmed.
"""

import argparse
import functools
import json
import os
import queue
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import torch
import torch.nn.functional as F

SAMPLE_RATE = 24000
# Frames before the first piece is decoded, and per piece after that. Twelve
# frames are a second of sound; three is a quarter, small enough to start
# quickly and large enough that the decoder has something to say.
FIRST_FRAMES = 3
PIECE_FRAMES = 8
# How many earlier frames each piece is decoded with, and then cut off again:
# what qwen-tts's chunked_decode uses.
LEFT_CONTEXT = 25

_LOG_LOCK = threading.Lock()


def log(msg):
    with _LOG_LOCK:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------
# the code predictor, replayed
# --------------------------------------------------------------------------

PREDICTOR_LENGTH = 16          # 2 prefill positions + 14 one-token steps


class GraphPredictor:
    """The code predictor's whole 15-token run as one CUDA graph.

    Every frame does the same thing at the same positions -- a prefill of
    [past_hidden, last_id_hidden] at 0-1, then 14 one-token steps at 2-15 -- so
    the run has fixed shapes end to end and replays without a single launch
    from Python. Greedy, it picks the same tokens as the original in 289 of 300
    (the rest are bf16 near-ties). Sampling is top-k then Gumbel-max, which
    draws from the same distribution as softmax + multinomial and is safe
    inside a graph.
    """

    def __init__(self, pred, top_k, temperature, do_sample):
        from qwen_tts.core.models.modeling_qwen3_tts import apply_rotary_pos_emb

        self.rope = apply_rotary_pos_emb
        self.pred = pred
        self.model = pred.model
        cfg = self.model.config
        self.layers = list(self.model.layers)
        self.steps = cfg.num_code_groups - 1
        self.top_k = int(top_k or 0)
        self.temperature = max(float(temperature or 1.0), 1e-5)
        self.do_sample = bool(do_sample)
        p = next(pred.parameters())
        dev, dt = p.device, p.dtype
        self.kv_heads = cfg.num_key_value_heads
        self.heads = cfg.num_attention_heads
        self.head_dim = cfg.head_dim
        proj = pred.small_to_mtp_projection
        width = proj.in_features if hasattr(proj, "in_features") else cfg.hidden_size
        self.inp = torch.zeros(1, 2, width, device=dev, dtype=dt)
        self.out = torch.zeros(1, self.steps, dtype=torch.long, device=dev)
        shape = (1, self.kv_heads, PREDICTOR_LENGTH, self.head_dim)
        self.k = [torch.zeros(shape, device=dev, dtype=dt) for _ in self.layers]
        self.v = [torch.zeros(shape, device=dev, dtype=dt) for _ in self.layers]
        self.cos, self.sin = self.model.rotary_emb(
            self.inp, torch.arange(PREDICTOR_LENGTH, device=dev)[None])
        n = torch.arange(PREDICTOR_LENGTH, device=dev)
        self.mask = n[None, :] <= n[:, None]           # [query, key] -> may see
        self.graph = None

    def _forward(self, x, start, n):
        cos, sin = self.cos[:, start:start + n], self.sin[:, start:start + n]
        mask = self.mask[start:start + n][None, None]
        for i, layer in enumerate(self.layers):
            att = layer.self_attn
            h = layer.input_layernorm(x)
            q = att.q_norm(att.q_proj(h).view(1, n, self.heads, self.head_dim)).transpose(1, 2)
            k = att.k_norm(att.k_proj(h).view(1, n, self.kv_heads, self.head_dim)).transpose(1, 2)
            v = att.v_proj(h).view(1, n, self.kv_heads, self.head_dim).transpose(1, 2)
            q, k = self.rope(q, k, cos, sin)
            self.k[i][:, :, start:start + n] = k
            self.v[i][:, :, start:start + n] = v
            o = F.scaled_dot_product_attention(q, self.k[i], self.v[i], attn_mask=mask,
                                               scale=att.scaling, enable_gqa=True)
            x = x + att.o_proj(o.transpose(1, 2).reshape(1, n, -1))
            x = x + layer.mlp(layer.post_attention_layernorm(x))
        return self.model.norm(x)

    def _pick(self, logits):
        if not self.do_sample:
            return logits.argmax(dim=-1, keepdim=True)
        logits = logits.float() / self.temperature
        if self.top_k:
            kth = torch.topk(logits, self.top_k, dim=-1).values[..., -1:]
            logits = logits.masked_fill(logits < kth, float("-inf"))
        u = torch.rand_like(logits).clamp_(1e-10, 1.0)
        return (logits - torch.log(-torch.log(u))).argmax(dim=-1, keepdim=True)

    def _run(self):
        x = self._forward(self.pred.small_to_mtp_projection(self.inp), 0, 2)
        tok = self._pick(self.pred.lm_head[0](x[:, -1]))
        self.out[:, 0:1] = tok
        tables = self.model.get_input_embeddings()
        for step in range(1, self.steps):
            e = self.pred.small_to_mtp_projection(tables[step - 1](tok))
            x = self._forward(e, step + 1, 1)
            tok = self._pick(self.pred.lm_head[step](x[:, -1]))
            self.out[:, step:step + 1] = tok

    @torch.inference_mode()
    def __call__(self, inputs_embeds):
        self.inp.copy_(inputs_embeds)
        if self.graph is None:
            side = torch.cuda.Stream()
            side.wait_stream(torch.cuda.current_stream())
            with torch.cuda.stream(side):
                for _ in range(2):                   # warm up outside the capture
                    self._run()
            torch.cuda.current_stream().wait_stream(side)
            self.graph = torch.cuda.CUDAGraph()
            with torch.cuda.graph(self.graph):
                self._run()
        self.graph.replay()
        return self.out.clone()


def _lean_generate(pred, inputs_embeds, max_new_tokens, do_sample, top_p, top_k, temperature):
    """The same run in plain Python, for the one case the graph does not take:
    top_p below 1. Still skips generate()'s setup, which was most of the cost."""
    from transformers.cache_utils import DynamicCache

    def pick(logits):
        if not do_sample:
            return logits.argmax(dim=-1, keepdim=True)
        logits = logits.float() / max(float(temperature or 1.0), 1e-5)
        if top_k:
            kth = torch.topk(logits, int(top_k), dim=-1).values[..., -1:]
            logits = logits.masked_fill(logits < kth, float("-inf"))
        sorted_logits, idx = torch.sort(logits, descending=True, dim=-1)
        probs = sorted_logits.softmax(dim=-1)
        sorted_logits = sorted_logits.masked_fill(probs.cumsum(-1) - probs > float(top_p), float("-inf"))
        logits = torch.full_like(logits, float("-inf")).scatter(-1, idx, sorted_logits)
        return torch.multinomial(logits.softmax(dim=-1), 1)

    cache = DynamicCache()
    out = pred.model(inputs_embeds=pred.small_to_mtp_projection(inputs_embeds),
                     past_key_values=cache, use_cache=True)
    tok = pick(pred.lm_head[0](out.last_hidden_state[:, -1]))
    tokens = [tok]
    tables = pred.model.get_input_embeddings()
    for step in range(1, max_new_tokens):
        out = pred.model(inputs_embeds=pred.small_to_mtp_projection(tables[step - 1](tok)),
                         past_key_values=cache, use_cache=True)
        tok = pick(pred.lm_head[step](out.last_hidden_state[:, -1]))
        tokens.append(tok)
    return torch.cat(tokens, dim=-1)


_graphs = {}


def install_graph_predictor():
    from qwen_tts.core.models import modeling_qwen3_tts as m

    @torch.inference_mode()
    def generate(self, inputs_embeds, max_new_tokens, do_sample=True, top_p=None,
                 top_k=None, temperature=None, **_ignored):
        if (inputs_embeds.shape[0] == 1 and inputs_embeds.shape[1] == 2
                and (top_p is None or float(top_p) >= 1.0)):
            key = (id(self), bool(do_sample), int(top_k or 0), float(temperature or 1.0))
            graph = _graphs.get(key)
            if graph is None:
                graph = _graphs[key] = GraphPredictor(self, top_k, temperature, do_sample)
            return SimpleNamespace(sequences=graph(inputs_embeds))
        return SimpleNamespace(sequences=_lean_generate(
            self, inputs_embeds, max_new_tokens, do_sample, top_p, top_k, temperature))

    m.Qwen3TTSTalkerCodePredictorModelForConditionalGeneration.generate = generate


# --------------------------------------------------------------------------
# frames, as the talker makes them
# --------------------------------------------------------------------------

class Stopped(Exception):
    """Raised inside generation when the caller has hung up."""


_sink = SimpleNamespace(frames=None, cancel=None)


def install_frame_tap(eos_id):
    """Every frame's 16 codes, handed to whoever is listening, as they are made.

    The talker's forward builds them -- the code it sampled last step and the
    fifteen the predictor added -- and returns them beside its hidden states,
    which is where qwen-tts itself collects them once generation is over. A
    frame whose first code is end-of-speech is not sound, and qwen-tts cuts it
    off the same way. One request runs at a time, so one sink is enough.
    """
    from qwen_tts.core.models import modeling_qwen3_tts as m

    talker = m.Qwen3TTSTalkerForConditionalGeneration
    original = talker.forward

    # wraps() is not cosmetic: transformers checks generate()'s keyword
    # arguments against forward's signature, and a bare (*args, **kwargs)
    # makes it refuse the talker's own ones as typos.
    @functools.wraps(original)
    def forward(self, *args, **kwargs):
        if _sink.cancel is not None and _sink.cancel.is_set():
            raise Stopped()
        out = original(self, *args, **kwargs)
        codes = out.hidden_states[1] if isinstance(out.hidden_states, tuple) else None
        if codes is not None and _sink.frames is not None:
            frame = codes[0].detach()
            if int(frame[0]) != eos_id:
                _sink.frames.put(frame.clone())
        return out

    talker.forward = forward


# --------------------------------------------------------------------------
# the model
# --------------------------------------------------------------------------

class Voice:
    """Studio's files, read once: what qwen-tts's prompt needs from them."""

    def __init__(self, item, ref_code=None):
        self.item = item
        self.ref_code = ref_code


class Speaker:
    def __init__(self, model_dir, graph=True):
        from qwen_tts import Qwen3TTSModel

        t = time.monotonic()
        log(f"loading {model_dir}")
        self.tts = Qwen3TTSModel.from_pretrained(model_dir, device_map="cuda:0",
                                                 dtype=torch.bfloat16, attn_implementation="sdpa")
        self.device = next(self.tts.model.parameters()).device
        self.decoder = self.tts.model.speech_tokenizer.model.decoder
        self.upsample = int(self.decoder.total_upsample)
        eos = self.tts.model.config.talker_config.codec_eos_token_id
        if graph:
            install_graph_predictor()
        install_frame_tap(eos)
        self.voices = {}
        self.lock = threading.Lock()
        log(f"loaded in {time.monotonic() - t:.1f}s, "
            f"{torch.cuda.memory_allocated() / 2**30:.2f} GB on the card"
            + ("" if graph else " (predictor not graphed)"))

    # -- voices --------------------------------------------------------------
    def voice(self, embedding=None, clip=None, clip_text=None):
        """A prompt item for a voice, from Studio's embedding.json or from a
        clip and its transcript. Cached by path and modification time."""
        from qwen_tts.inference.qwen3_tts_model import VoiceClonePromptItem

        if clip and clip_text:
            key = ("clip", clip, os.path.getmtime(clip), clip_text)
        elif embedding:
            key = ("embedding", embedding, os.path.getmtime(embedding))
        else:
            raise ValueError("a voice needs an embedding, or a clip and its words")
        hit = self.voices.get(key)
        if hit is not None:
            return hit
        if key[0] == "clip":
            item = self.tts.create_voice_clone_prompt(ref_audio=clip, ref_text=clip_text)[0]
            hit = Voice(item, item.ref_code)
        else:
            with open(embedding, encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):                  # an icl-prompt.json carries one too
                raw = raw.get("speaker_embedding") or raw.get("embedding")
            emb = torch.tensor(raw, dtype=torch.bfloat16, device=self.device)
            hit = Voice(VoiceClonePromptItem(ref_code=None, ref_spk_embedding=emb,
                                             x_vector_only_mode=True, icl_mode=False))
        self.voices[key] = hit
        return hit

    # -- one line --------------------------------------------------------------
    def _prepare(self, text, language, voice):
        """What generate_voice_clone hands to the model, for one line."""
        tts = self.tts
        prompt = tts._prompt_items_to_voice_clone_prompt([voice.item])
        input_ids = tts._tokenize_texts([tts._build_assistant_text(text)])
        ref_ids = None
        if voice.item.ref_text:
            ref_ids = [tts._tokenize_texts([tts._build_ref_text(voice.item.ref_text)])[0]]
        else:
            ref_ids = [None]
        return dict(input_ids=input_ids, ref_ids=ref_ids, voice_clone_prompt=prompt,
                    languages=[language or "Auto"], non_streaming_mode=False,
                    **tts._merge_generate_kwargs())

    def stream(self, text, language, voice, write, seed=None):
        """Generate on a thread of its own; decode and write pieces on this one.

        `write(bytes)` raising (the caller hung up) stops the generation at
        the next frame. Returns (frames, seconds of sound).
        """
        torch.manual_seed(seed if seed is not None else random.randrange(1, 2**31 - 1))
        frames = queue.Queue()
        cancel = threading.Event()
        done = object()
        failure = []
        kwargs = self._prepare(text, language, voice)

        def run():
            try:
                with torch.inference_mode():
                    self.tts.model.generate(**kwargs)
            except Stopped:
                pass
            except BaseException as exc:              # noqa: BLE001
                failure.append(exc)
            finally:
                frames.put(done)

        _sink.frames, _sink.cancel = frames, cancel
        worker = threading.Thread(target=run, name="qwen-generate", daemon=True)
        worker.start()
        # Left context for the first piece: the clip's own last frames, when
        # the voice came from one -- the decoder then starts mid-voice rather
        # than from silence, which is what qwen-tts does by decoding them together.
        context = []
        if voice.ref_code is not None:
            context = [f for f in voice.ref_code[-LEFT_CONTEXT:].to(self.device)]
        new, made, finished = [], 0, False
        try:
            while not finished:
                frame = frames.get()
                if frame is done:
                    finished = True
                else:
                    new.append(frame)
                want = FIRST_FRAMES if made == 0 else PIECE_FRAMES
                if new and (finished or len(new) >= want):
                    pcm = self._decode(context, new)
                    made += len(new)
                    context = (context + new)[-LEFT_CONTEXT:]
                    new = []
                    write(pcm)
        except BaseException:
            cancel.set()
            raise
        finally:
            worker.join()
            _sink.frames, _sink.cancel = None, None
        if failure:
            raise failure[0]
        return made, made * self.upsample / SAMPLE_RATE

    @torch.inference_mode()
    def _decode(self, context, new):
        codes = torch.stack(context + new).clamp(min=0)          # (n, 16)
        wav = self.decoder(codes.T.unsqueeze(0)).clamp(-1, 1)    # (1, 1, samples)
        wav = wav[0, 0, len(context) * self.upsample:]
        return (wav.float() * 32767.0).round().to(torch.int16).cpu().numpy().tobytes()


# --------------------------------------------------------------------------
# the HTTP side
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"       # the body ends when the connection closes
    speaker = None
    ready = False

    def log_message(self, fmt, *args):
        pass                            # one line per request is ours to write

    def _json(self, code, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            if Handler.ready:
                return self._json(200, {"ok": True, "sampleRate": SAMPLE_RATE})
            return self._json(503, {"ok": False})
        self._json(404, {"error": "no such route"})

    def do_POST(self):
        if self.path != "/speak":
            return self._json(404, {"error": "no such route"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return self._json(400, {"error": "a JSON body is needed"})
        text = (req.get("text") or "").strip()
        if not text:
            return self._json(400, {"error": "nothing to say"})
        sp = Handler.speaker
        if not sp.lock.acquire(blocking=False):
            return self._json(409, {"error": "busy with another line"})
        try:
            try:
                voice = sp.voice(req.get("embedding"), req.get("clip"), req.get("clipText"))
            except (OSError, ValueError) as exc:
                return self._json(400, {"error": f"the voice could not be read: {exc}"})
            self.send_response(200)
            self.send_header("Content-Type", "audio/L16; rate=24000; channels=1")
            self.end_headers()
            began = time.monotonic()
            first = []

            def write(pcm):
                if not first:
                    first.append(time.monotonic() - began)
                self.wfile.write(pcm)
                self.wfile.flush()

            try:
                frames, seconds = sp.stream(text, req.get("language"), voice, write,
                                            req.get("seed"))
                took = time.monotonic() - began
                log(f"said {len(text)} chars: {seconds:.1f}s of sound in {took:.1f}s "
                    f"(x{seconds / max(took, 1e-6):.2f}), first sound at "
                    f"{(first or [0])[0]:.2f}s")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                log("the caller hung up; stopped")
            except Exception as exc:                   # noqa: BLE001
                log(f"failed: {type(exc).__name__}: {exc}")
        finally:
            sp.lock.release()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", required=True, help="the Qwen3-TTS Base folder")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--no-graph", action="store_true", help="leave the predictor as qwen-tts has it")
    ap.add_argument("--warm-voice", help="an embedding.json to say a first word with")
    a = ap.parse_args()

    if not torch.cuda.is_available():
        log("no CUDA device: Qwen on PyTorch needs an NVIDIA card")
        sys.exit(2)
    log(f"torch {torch.__version__} on {torch.cuda.get_device_name(0)}")
    Handler.speaker = Speaker(a.model, graph=not a.no_graph)
    server = ThreadingHTTPServer((a.host, a.port), Handler)
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    log(f"listening on http://{a.host}:{a.port}")
    # Capture the graph and pay CUDA's first-use costs before saying ready, so
    # the first real line is not the one that waits.
    if a.warm_voice and os.path.isfile(a.warm_voice):
        t = time.monotonic()
        try:
            voice = Handler.speaker.voice(embedding=a.warm_voice)
            Handler.speaker.stream("Hi.", "English", voice, lambda _pcm: None, seed=1)
            log(f"warmed up in {time.monotonic() - t:.1f}s")
        except Exception as exc:                       # noqa: BLE001
            log(f"warm-up failed ({exc}); carrying on")
    Handler.ready = True
    log("ready")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
