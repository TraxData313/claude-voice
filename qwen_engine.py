"""
Qwen-TTS Studio, driven headlessly.

Studio ships no CLI: its --dump-speaker-embedding fallback wants a tts-cli binary
that is not in the box, and everything else is GUI. So this starts Studio's own
bundled JVM in-process (ctypes -> JNI_CreateJavaVM), instantiates
com.qwen.tts.studio.engine.QwenEngine off the app's jars, and calls its JNI
methods directly. Verified equivalent to the GUI: re-extracting a voice this way
produced an embedding.json byte-identical to the one Studio wrote.

Two things here are exact rather than approximate, and both cost real time to
find, so leave them alone unless you enjoy debugging heap corruption:

  * JNI function-table slots. CallLongMethodA is 54, not 51 -- 51 is
    CallIntMethodA, which silently truncates the 64-bit engine handle to 32 bits
    and then crashes deep inside qwen3_tts.dll at model load.
  * nativePtr must be set by hand after nativeInit. The Kotlin wrappers such as
    generate() read the handle off that field and normally only loadDetailed()
    sets it; leaving it zero passes a null context into native code.

Also: the embedding extractor and the ICL prompt encoder cannot share a process.
Loading the ICL encoder tears the talker model down underneath the engine.
"""

import ctypes
import os
from ctypes import (CFUNCTYPE, POINTER, c_char_p, c_float, c_int, c_int64,
                    c_uint8, c_void_p)

JNI_VERSION_1_8 = 0x00010008

# Slot numbers in the JNINativeInterface function table (jni.h order).
FIND_CLASS = 6
EXCEPTION_OCCURRED = 15
EXCEPTION_DESCRIBE = 16
EXCEPTION_CLEAR = 17
DELETE_LOCAL_REF = 23
NEW_OBJECT_A = 30
GET_METHOD_ID = 33
CALL_OBJECT_METHOD_A = 36
CALL_BOOLEAN_METHOD_A = 39
CALL_LONG_METHOD_A = 54
CALL_VOID_METHOD_A = 63
GET_FIELD_ID = 94
SET_LONG_FIELD = 110
GET_STATIC_FIELD_ID = 144
GET_STATIC_OBJECT_FIELD = 145
NEW_STRING_UTF = 167
GET_STRING_UTF_CHARS = 169
RELEASE_STRING_UTF_CHARS = 170
GET_ARRAY_LENGTH = 171
GET_FLOAT_ARRAY_REGION = 205

SAMPLE_RATE = 24000          # what the vocoder reports on load


# ---------------------------------------------------------------------------
# The DLL's own C ABI, beside the JNI wrappers we drive everything else through.
#
# We come down here for one reason: streaming. Speaking an answer as several
# separate generations means the model rolls its prosody afresh for each, and
# the same voice comes back as a recognisably different person at every seam.
# One streaming generation hands its audio over in pieces as they are made, so
# the timbre is continuous by construction and the first words arrive sooner
# than a whole first sentence could be synthesised. The Kotlin 'generate'
# wrapper cannot do it: it takes no parameter block and returns one array.
#
# Layouts below are copied from the sister project's host, where they were
# derived from the disassembly of these same JNI wrappers and then proven over
# 200 consecutive syntheses. Do not tidy them. A wrong offset does not raise --
# it reads whatever else is there, and ggml's answer to bad input is abort().
# ---------------------------------------------------------------------------


class QwenResult(ctypes.Structure):
    """40 bytes (0x28). Returned by value, so through a hidden pointer on x64."""
    _fields_ = [
        ("audio", POINTER(c_float)),   # 0x00  mono float32, owned by the result
        ("num_samples", c_int),        # 0x08
        ("sample_rate", c_int),        # 0x0C
        ("success", c_int),            # 0x10  nonzero is ok
        ("_reserved14", c_int),        # 0x14
        ("error", c_char_p),           # 0x18  UTF-8, owned by the result
        ("time_ms", c_int64),          # 0x20
    ]


class QwenParams(ctypes.Structure):
    """The parameter block. Plain calls read the first 64 bytes, streaming reads
    80 -- the extra 16 carry the chunking knobs. We always pass all 80, zeroed,
    which is safe for both.

    Two fields Studio itself leaves uninitialised, so it passes whatever was on
    its stack. Zero them: 0x24 and 0x3C.
    """
    _fields_ = [
        ("max_audio_tokens", c_int),        # 0x00  Studio default 4096 = 327.68 s
        ("temperature", c_float),           # 0x04  Studio hardcodes 0.9
        ("top_p", c_float),                 # 0x08  Studio hardcodes 1.0
        ("top_k", c_int),                   # 0x0C  Studio hardcodes 50
        ("threads", c_int),                 # 0x10  Studio hardcodes 4
        ("_unknown14", c_int),              # 0x14  Studio writes 0
        ("_unknown18", c_int),              # 0x18  Studio writes 1
        ("_unknown1c", c_float),            # 0x1C  Studio writes 1.05 (repetition penalty?)
        ("language_id", c_int),             # 0x20  -1 auto; en 2050, ru 2069
        ("_unknown24", c_int),              # 0x24  UNINITIALISED in Studio; zero it
        ("instruction", c_void_p),          # 0x28  const char* or null
        ("speaker", c_void_p),              # 0x30  const char* or null
        ("_unknown38", c_float),            # 0x38  Studio writes 2.0
        ("_unknown3c", c_int),              # 0x3C  UNINITIALISED in Studio; zero it
        ("chunk_seconds", c_float),         # 0x40  streaming only
        ("left_context_seconds", c_float),  # 0x44  streaming only
        ("collect_audio", c_int),           # 0x48  streaming only
        ("_pad4c", c_int),                  # 0x4C
    ]

    @classmethod
    def studio_defaults(cls, language_id=-1, max_audio_tokens=4096):
        return cls(max_audio_tokens=max_audio_tokens, temperature=0.9, top_p=1.0,
                   top_k=50, threads=4, _unknown14=0, _unknown18=1, _unknown1c=1.05,
                   language_id=language_id, _unknown24=0, instruction=None,
                   speaker=None, _unknown38=2.0, _unknown3c=0,
                   chunk_seconds=0.0, left_context_seconds=0.0, collect_audio=0,
                   _pad4c=0)


class QwenChunk(ctypes.Structure):
    """One streamed piece. 56 bytes (0x38).

    The text-byte fields are the useful part beyond the audio: they say which
    slice of the input this piece covers, which is how playback can be cut at a
    sentence end rather than mid-word.
    """
    _fields_ = [
        ("audio", POINTER(c_float)),   # 0x00  borrowed -- copy it before returning
        ("num_samples", c_int),        # 0x08
        ("sample_rate", c_int),        # 0x0C
        ("start_sample", c_int64),     # 0x10
        ("end_sample", c_int64),       # 0x18
        ("start_frame", c_int),        # 0x20
        ("end_frame", c_int),          # 0x24
        ("start_text_byte", c_int),    # 0x28
        ("end_text_byte", c_int),      # 0x2C
        ("alignment_kind", c_int),     # 0x30
        ("confidence", c_float),       # 0x34
    ]


# Return nonzero to carry on, zero to ask the engine to stop early. Confirmed
# from the DLL's own JNI trampoline: it receives exactly (chunk*, user_data).
QWEN_CHUNK_CALLBACK = CFUNCTYPE(c_int, c_void_p, c_void_p)

# About a second is the sweet spot found live: long enough that making one never
# starves playback, short enough that the first words arrive well inside a second.
CHUNK_SECONDS = 1.0
# How much already-spoken audio the engine keeps as context for the next piece.
# This is the thing that carries the voice across the seams.
LEFT_CONTEXT_SECONDS = 2.0
# Measured, and the number the whole ceiling rests on: asked for 256 tokens, the
# engine returned 491,520 samples -- 20.48 s -- twice, from two different texts.
SAMPLES_PER_TOKEN = 1920


class JavaVMOption(ctypes.Structure):
    _fields_ = [("optionString", c_char_p), ("extraInfo", c_void_p)]


class JavaVMInitArgs(ctypes.Structure):
    _fields_ = [
        ("version", c_int),
        ("nOptions", c_int),
        ("options", POINTER(JavaVMOption)),
        ("ignoreUnrecognized", c_uint8),
    ]


class JValue(ctypes.Union):
    _fields_ = [
        ("z", c_uint8), ("b", ctypes.c_int8), ("c", ctypes.c_uint16),
        ("s", ctypes.c_int16), ("i", c_int), ("j", c_int64),
        ("f", ctypes.c_float), ("d", ctypes.c_double), ("l", c_void_p),
    ]


class Jint(int):
    """Marks a value that must land in the jvalue's 32-bit int slot, not the long one."""


class Jni:
    """Thin ctypes wrapper over the JNIEnv function table."""

    def __init__(self, env):
        self.env = env
        self.table = ctypes.cast(env, POINTER(POINTER(c_void_p))).contents

    def _fn(self, slot, restype, argtypes):
        return ctypes.CFUNCTYPE(restype, *argtypes)(self.table[slot])

    def check(self, what):
        exc = self._fn(EXCEPTION_OCCURRED, c_void_p, [c_void_p])(self.env)
        if exc:
            self._fn(EXCEPTION_DESCRIBE, None, [c_void_p])(self.env)
            self._fn(EXCEPTION_CLEAR, None, [c_void_p])(self.env)
            raise RuntimeError(f"java exception during {what}")

    def find_class(self, name):
        cls = self._fn(FIND_CLASS, c_void_p, [c_void_p, c_char_p])(self.env, name.encode())
        self.check(f"FindClass {name}")
        if not cls:
            raise RuntimeError(f"class not found: {name}")
        return cls

    def method_id(self, cls, name, sig):
        mid = self._fn(GET_METHOD_ID, c_void_p, [c_void_p, c_void_p, c_char_p, c_char_p])(
            self.env, cls, name.encode(), sig.encode())
        self.check(f"GetMethodID {name}{sig}")
        if not mid:
            raise RuntimeError(f"method not found: {name}{sig}")
        return mid

    def set_long_field(self, obj, cls, name, value):
        fid = self._fn(GET_FIELD_ID, c_void_p, [c_void_p, c_void_p, c_char_p, c_char_p])(
            self.env, cls, name.encode(), b"J")
        self.check(f"GetFieldID {name}")
        self._fn(SET_LONG_FIELD, None, [c_void_p, c_void_p, c_void_p, c_int64])(
            self.env, obj, fid, value)
        self.check(f"SetLongField {name}")

    def static_object_field(self, cls, name, sig):
        fid = self._fn(GET_STATIC_FIELD_ID, c_void_p,
                       [c_void_p, c_void_p, c_char_p, c_char_p])(
            self.env, cls, name.encode(), sig.encode())
        self.check(f"GetStaticFieldID {name}")
        obj = self._fn(GET_STATIC_OBJECT_FIELD, c_void_p, [c_void_p, c_void_p, c_void_p])(
            self.env, cls, fid)
        self.check(f"GetStaticObjectField {name}")
        return obj

    def new_string(self, s):
        if s is None:
            return None
        ptr = self._fn(NEW_STRING_UTF, c_void_p, [c_void_p, c_char_p])(
            self.env, s.encode("utf-8"))
        self.check("NewStringUTF")
        return ptr

    def to_str(self, jstr):
        if not jstr:
            return None
        chars = self._fn(GET_STRING_UTF_CHARS, c_char_p, [c_void_p, c_void_p, c_void_p])(
            self.env, jstr, None)
        out = chars.decode("utf-8", "replace") if chars else None
        self._fn(RELEASE_STRING_UTF_CHARS, None, [c_void_p, c_void_p, c_char_p])(
            self.env, jstr, chars)
        return out

    def float_array(self, jarr):
        n = self._fn(GET_ARRAY_LENGTH, c_int, [c_void_p, c_void_p])(self.env, jarr)
        buf = (ctypes.c_float * n)()
        self._fn(GET_FLOAT_ARRAY_REGION, None,
                 [c_void_p, c_void_p, c_int, c_int, c_void_p])(self.env, jarr, 0, n, buf)
        self.check("GetFloatArrayRegion")
        return buf

    def _args(self, values):
        if not values:
            return None
        arr = (JValue * len(values))()
        for i, v in enumerate(values):
            if v is None:
                arr[i].j = 0
            elif isinstance(v, Jint):
                arr[i].i = int(v)
            elif isinstance(v, int):
                arr[i].j = v
            else:
                arr[i].l = v
        return arr

    def new_object(self, cls, mid, *values):
        obj = self._fn(NEW_OBJECT_A, c_void_p, [c_void_p, c_void_p, c_void_p, c_void_p])(
            self.env, cls, mid, self._args(values))
        self.check("NewObject")
        return obj

    def call_long(self, obj, mid, *values):
        r = self._fn(CALL_LONG_METHOD_A, c_int64, [c_void_p, c_void_p, c_void_p, c_void_p])(
            self.env, obj, mid, self._args(values))
        self.check("CallLongMethod")
        return r

    def call_bool(self, obj, mid, *values):
        r = self._fn(CALL_BOOLEAN_METHOD_A, c_uint8, [c_void_p, c_void_p, c_void_p, c_void_p])(
            self.env, obj, mid, self._args(values))
        self.check("CallBooleanMethod")
        return bool(r)

    def call_object(self, obj, mid, *values):
        r = self._fn(CALL_OBJECT_METHOD_A, c_void_p, [c_void_p, c_void_p, c_void_p, c_void_p])(
            self.env, obj, mid, self._args(values))
        self.check("CallObjectMethod")
        return r

    def call_void(self, obj, mid, *values):
        self._fn(CALL_VOID_METHOD_A, None, [c_void_p, c_void_p, c_void_p, c_void_p])(
            self.env, obj, mid, self._args(values))
        self.check("CallVoidMethod")

    def release(self, ref):
        if ref:
            self._fn(DELETE_LOCAL_REF, None, [c_void_p, c_void_p])(self.env, ref)


class Engine:
    """QwenEngine, minus the user interface.

    The JNIEnv handed back by JNI_CreateJavaVM belongs to the thread that made
    it. Create this on one thread and drive it from that same thread only.
    """

    def __init__(self, studio_dir, verbose=True):
        self.verbose = verbose
        self.studio_dir = studio_dir
        app_dir = os.path.join(studio_dir, "app")
        jvm_dll = os.path.join(studio_dir, "runtime", "bin", "server", "jvm.dll")

        for path, what in ((studio_dir, "Qwen-TTS Studio folder"),
                           (app_dir, "Studio's app\\ folder"),
                           (jvm_dll, "Studio's bundled JVM")):
            if not os.path.exists(path):
                raise FileNotFoundError(f"{what} not found: {path}")

        # ggml / cuBLAS live next to qwen3_tts.dll and are found via PATH.
        os.environ["PATH"] = studio_dir + os.pathsep + os.environ.get("PATH", "")
        os.add_dll_directory(studio_dir)

        jars = [os.path.join(app_dir, f)
                for f in sorted(os.listdir(app_dir)) if f.endswith(".jar")]
        if not jars:
            raise FileNotFoundError(f"no jars in {app_dir}")

        jvm = ctypes.CDLL(jvm_dll)
        opts = [
            f"-Djava.class.path={';'.join(jars)}".encode(),
            f"-Djava.library.path={studio_dir}".encode(),
            # QwenEngine.resolveNativeRoot() looks for qwen3_tts.dll here
            f"-Djna.library.path={studio_dir}".encode(),
            b"-Djna.protected=false",
            b"-Xss16m",
            b"--enable-native-access=ALL-UNNAMED",
        ]
        arr = (JavaVMOption * len(opts))()
        for i, o in enumerate(opts):
            arr[i].optionString = o
            arr[i].extraInfo = None

        args = JavaVMInitArgs(JNI_VERSION_1_8, len(opts), arr, 0)
        pvm, penv = c_void_p(), c_void_p()
        rc = jvm.JNI_CreateJavaVM(ctypes.byref(pvm), ctypes.byref(penv), ctypes.byref(args))
        if rc != 0:
            raise RuntimeError(f"JNI_CreateJavaVM failed: {rc}")

        self.jni = Jni(penv)
        cls = self.jni.find_class("com/qwen/tts/studio/engine/QwenEngine")
        self.engine = self.jni.new_object(cls, self.jni.method_id(cls, "<init>", "()V"))

        # The app loads ggml/CUDA and then qwen3_tts.dll in a specific order;
        # borrow its own routine rather than guessing at it.
        companion_cls = self.jni.find_class("com/qwen/tts/studio/engine/QwenEngine$Companion")
        companion = self.jni.static_object_field(
            cls, "Companion", "Lcom/qwen/tts/studio/engine/QwenEngine$Companion;")
        self.jni.call_void(companion, self.jni.method_id(companion_cls,
                                                        "ensureNativeLoaded", "()V"))
        self._log("native library loaded")

        m = lambda n, s: self.jni.method_id(cls, n, s)
        self._init = m("nativeInit", "()J")
        self._free = m("nativeFree", "(J)V")
        self._load = m("nativeLoadModels", "(JLjava/lang/String;Ljava/lang/String;)Z")
        self._load_icl = m("nativeLoadIclPromptEncoder",
                           "(JLjava/lang/String;Ljava/lang/String;)Z")
        self._embed = m("nativeExtractSpeakerEmbedding",
                        "(JLjava/lang/String;Ljava/lang/String;)Z")
        self._icl = m("nativeExtractIclPrompt",
                      "(JLjava/lang/String;Ljava/lang/String;Ljava/lang/String;)Z")
        self._err = m("nativeGetLastError", "(J)Ljava/lang/String;")

        self.cls = cls
        self.handle = self.jni.call_long(self.engine, self._init)
        if not self.handle:
            raise RuntimeError("nativeInit returned 0")

        # Publish the handle by hand or the Kotlin wrappers pass a null context.
        self.jni.set_long_field(self.engine, cls, "nativePtr", self.handle)

    def _log(self, msg):
        if self.verbose:
            print(f"[engine] {msg}", flush=True)

    def _call_bool(self, mid, *strings):
        refs = [self.jni.new_string(s) for s in strings]
        try:
            return self.jni.call_bool(self.engine, mid, self.handle, *refs)
        finally:
            for r in refs:
                self.jni.release(r)

    def last_error(self):
        js = self.jni.call_object(self.engine, self._err, self.handle)
        try:
            return self.jni.to_str(js) or "(no detail)"
        finally:
            self.jni.release(js)

    def load_models(self, model_dir, model_name):
        self._log(f"loading {model_name}")
        if not self._call_bool(self._load, model_dir, model_name):
            raise RuntimeError(f"nativeLoadModels failed: {self.last_error()}")
        self._log("models loaded")

    def load_icl_encoder(self, model_dir, tokenizer_name):
        """Note: this unloads the talker model. Not in the same process as synthesis."""
        self._log(f"loading ICL encoder {tokenizer_name}")
        if not self._call_bool(self._load_icl, model_dir, tokenizer_name):
            raise RuntimeError(f"nativeLoadIclPromptEncoder failed: {self.last_error()}")

    def extract_embedding(self, wav, out_path):
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        if not self._call_bool(self._embed, wav, out_path):
            raise RuntimeError(f"embedding extraction failed: {self.last_error()}")
        return out_path

    def extract_icl_prompt(self, wav, reference_text, out_path):
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        if not self._call_bool(self._icl, wav, reference_text, out_path):
            raise RuntimeError(f"ICL extraction failed: {self.last_error()}")
        return out_path

    # -- streaming, through the C ABI --------------------------------------

    def _abi(self):
        """Bind the DLL's own exports, lazily and once.

        The JVM has already loaded this module -- its own ensureNativeLoaded()
        did it, in the order ggml and CUDA need -- so opening it again by path
        hands back the same module rather than a second copy of anything. And
        the context we pass in is the one nativeInit gave us, which is what the
        JNI wrappers have been forwarding as `ctx` all along.
        """
        if getattr(self, "_dll", None) is None:
            dll = ctypes.CDLL(os.path.join(self.studio_dir, "qwen3_tts.dll"))
            sig = [c_void_p, c_char_p, c_char_p, POINTER(QwenParams),
                   QWEN_CHUNK_CALLBACK, c_void_p]
            for name in ("qwen3_tts_synthesize_with_speaker_embedding_streaming",
                         "qwen3_tts_synthesize_with_icl_prompt_streaming"):
                fn = getattr(dll, name)
                fn.restype = QwenResult
                fn.argtypes = sig
            dll.qwen3_tts_free_result.restype = None
            dll.qwen3_tts_free_result.argtypes = [POINTER(QwenResult)]
            self._dll = dll
        return self._dll

    def synthesize_streaming(self, text, on_piece, embedding_path=None,
                             icl_prompt_path=None, language_id=-1,
                             max_seconds=None):
        """One generation, handed over in pieces as it is made.

        `on_piece(samples, chunk)` is called for each piece, on this thread,
        while the generation is still running. Return False from it to stop the
        engine early -- that is the DLL's own documented way of being asked to
        stop, and it is how the derail guard pulls the cord here.

        This is not the same as calling synthesize() several times. The model
        rolls its prosody afresh for every generation, so several calls give
        several subtly different speakers; one streaming call keeps two seconds
        of what it has already said as context for the next piece, which is what
        carries the voice across a seam unchanged.

        Returns how many samples were handed over. Truncation is safe: a derail
        drifts, so everything already handed over is good speech.
        """
        dll = self._abi()
        if not embedding_path and not icl_prompt_path:
            raise ValueError("streaming needs an embedding or an ICL prompt")

        # Two ceilings that do not depend on each other. The engine's own is
        # exact -- ask for 256 tokens and 20.48 s is what comes back -- but it
        # depends on the field still meaning what it means, so the sample count
        # below trusts nothing and counts what actually arrives.
        seconds = max_seconds or 327.68
        tokens = max(40, min(4096, int(seconds * SAMPLE_RATE / SAMPLES_PER_TOKEN) + 1))
        budget = int(tokens * SAMPLES_PER_TOKEN * 1.1) + SAMPLE_RATE

        params = QwenParams.studio_defaults(language_id, tokens)
        params.chunk_seconds = CHUNK_SECONDS
        params.left_context_seconds = LEFT_CONTEXT_SECONDS
        params.collect_audio = 0        # the pieces are the output; do not keep a second copy

        state = {"samples": 0, "pieces": 0, "stopped": False, "error": None}

        def _on_chunk(chunk_ptr, _user):
            # Nothing may escape from here into native code: an exception
            # unwinding through a C stack frame is undefined behaviour, and
            # ggml's answer to a bad state is abort(), which no caller survives.
            try:
                if not chunk_ptr:
                    return 1
                c = ctypes.cast(chunk_ptr, POINTER(QwenChunk)).contents
                n = c.num_samples
                if not c.audio or n <= 0 or n > 50_000_000:
                    return 1
                state["samples"] += n
                state["pieces"] += 1
                if on_piece(c.audio[0:n], c) is False:
                    state["stopped"] = True
                    return 0
                if state["samples"] > budget:
                    state["stopped"] = True
                    self._log(f"derail guard: {state['samples']} samples for "
                              f"{len(text)} characters -- stopping it here")
                    return 0
                return 1
            except BaseException as exc:            # noqa: BLE001 -- see above
                state["error"] = exc
                return 0

        cb = QWEN_CHUNK_CALLBACK(_on_chunk)         # must outlive the call
        call = (dll.qwen3_tts_synthesize_with_icl_prompt_streaming if icl_prompt_path
                else dll.qwen3_tts_synthesize_with_speaker_embedding_streaming)
        voice = (icl_prompt_path or embedding_path).encode("utf-8")

        result = call(c_void_p(self.handle), text.encode("utf-8"), voice,
                      ctypes.byref(params), cb, None)
        try:
            if state["error"] is not None:
                raise state["error"]
            # A generation we stopped ourselves reports failure, and rightly so.
            # Only complain when nothing was handed over at all.
            if not result.success and not state["stopped"]:
                detail = (result.error or b"").decode("utf-8", "replace") or self.last_error()
                raise RuntimeError(f"streaming synthesis failed: {detail}")
        finally:
            dll.qwen3_tts_free_result(ctypes.byref(result))
        return state["samples"]

    def synthesize(self, text, embedding_path=None, icl_prompt_path=None,
                   reference_wav=None, language_id=-1):
        """Speak `text` in the given voice. Returns mono float samples at SAMPLE_RATE."""
        mid = self.jni.method_id(
            self.cls, "generate",
            "(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;"
            "Ljava/lang/String;ILjava/lang/String;Ljava/lang/String;)[F")

        refs = [self.jni.new_string(s) for s in
                (text, reference_wav, embedding_path, icl_prompt_path)]
        try:
            arr = self.jni.call_object(
                self.engine, mid, refs[0], refs[1], refs[2], refs[3],
                Jint(language_id), None, None)
            if not arr:
                raise RuntimeError(f"generate returned null: {self.last_error()}")
            try:
                return self.jni.float_array(arr)
            finally:
                self.jni.release(arr)
        finally:
            for r in refs:
                self.jni.release(r)

    def close(self):
        if getattr(self, "handle", None):
            self.jni.call_void(self.engine, self._free, self.handle)
            self.handle = 0


def write_wav(path, samples, rate=SAMPLE_RATE):
    import wave

    peak = max((abs(s) for s in samples), default=0.0)
    scale = (0.99 / peak) if peak > 1.0 else 1.0
    pcm = bytearray()
    for s in samples:
        v = int(max(-1.0, min(1.0, s * scale)) * 32767)
        pcm += v.to_bytes(2, "little", signed=True)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with wave.open(path, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(bytes(pcm))
    return len(samples) / rate
