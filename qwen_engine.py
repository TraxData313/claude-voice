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
from ctypes import POINTER, c_char_p, c_int, c_int64, c_uint8, c_void_p

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
