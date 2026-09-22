"""
Pocket TTS -- Kyutai's small one -- as a second engine.

Why it is here at all. The Qwen road in qwen_engine.py wants Studio installed,
a GPU, and a page of JNI sorcery to reach a DLL that ships no CLI. That is fine
on the machine it was written on and close to uninstallable anywhere else. This
is `pip install pocket-tts` and a CPU, which is the difference between a tool
one person runs and a tool anybody can.

It is not a consolation prize either. Measured here, on the CPU, against the
numbers docs/engine-notes.md records for the GPU engine:

    time to first audio     181 ms      against 812 ms
    throughput              4.0x        against 3.7-4.0x

What it costs is languages. Six, all Latin-script: english, french, german,
portuguese, italian, spanish. There is no Cyrillic in the model, so a Bulgarian
or Russian answer that reads today will not read here -- see docs/languages.md
for what the other engine does with those.

The interface is the one speak_server.py already calls -- synthesize_streaming,
synthesize, close, and a module-level SAMPLE_RATE -- so the server chooses an
engine at the top and does not care afterwards which one it got.

SAMPLE_RATE being 24000 for both is luck rather than planning, and worth saying
out loud because a great deal leans on it: the player, write_wav, and every
silence measurement in voice_lib are written against that number. An engine
added later at some other rate has to resample here, in this file, rather than
teach the rest of the tool a second rate.
"""

import os

SAMPLE_RATE = 24000          # what the model emits, and what everything downstream assumes

DEFAULT_LANGUAGE = "english"
DEFAULT_VOICE = "alba"

# The voices that ship with the model: who they are, and how they read.
#
# **None of this is in the package.** There is no gender field, no style field
# and no metadata file anywhere in `pocket_tts` -- only a dict of names mapped
# to the wav each was made from. So this table was transcribed from Kyutai's
# own listing, and that matters, because the obvious shortcut is wrong: reading
# the sex off the name gets `alba` backwards. She is not a she.
#
# `style` is the register the voice was recorded in. It is the more useful of
# the two columns and the one you cannot guess at all: the LibriVox readers
# (bill_boerst, peter_yearsley, stuart_bell, caro_davy) narrate, while most of
# the VCTK speakers talk -- but three of those VCTK voices narrate too, and
# nothing about the name says which. Worth having in the dropdown for the same
# reason: this tool reads summaries aloud, and a narrator and a talker are
# genuinely different to listen to for that.
#
# Three voices carry no style: cosette, marius and javert were not in the
# listing this came from. Their sex follows the Les Misérables naming that
# holds everywhere else here, and is marked as inference rather than as fact.
# An empty style prints nothing rather than a guess.
#
# The last five belong to the non-English models; the English model has its own
# precomputed state for each of the other twenty-one.
VOICES = [
    # id                sex       style           language
    ("alba",            "male",   "reading",      "english"),
    ("anna",            "female", "conversation", "english"),
    ("azelma",          "female", "reading",      "english"),
    ("bill_boerst",     "male",   "reading",      "english"),
    ("caro_davy",       "female", "reading",      "english"),
    ("charles",         "male",   "conversation", "english"),
    ("cosette",         "female", "",             "english"),   # sex inferred
    ("eponine",         "female", "reading",      "english"),
    ("eve",             "female", "conversation", "english"),
    ("fantine",         "female", "reading",      "english"),
    ("george",          "male",   "conversation", "english"),
    ("jane",            "female", "conversation", "english"),
    ("javert",          "male",   "",             "english"),   # sex inferred
    ("jean",            "male",   "conversation", "english"),
    ("marius",          "male",   "",             "english"),   # sex inferred
    ("mary",            "female", "conversation", "english"),
    ("michael",         "male",   "conversation", "english"),
    ("paul",            "male",   "conversation", "english"),
    ("peter_yearsley",  "male",   "reading",      "english"),
    ("stuart_bell",     "male",   "reading",      "english"),
    ("vera",            "female", "conversation", "english"),
    ("estelle",         "female", "",             "french"),
    ("giovanni",        "male",   "",             "italian"),
    ("juergen",         "male",   "",             "german"),
    ("lola",            "female", "",             "spanish"),
    ("rafael",          "male",   "",             "portuguese"),
]

LANGUAGES = ("english", "french", "german", "italian", "portuguese", "spanish")

# As the dropdown says them. Portuguese is Kyutai's own wording for rafael.
LANGUAGE_NAMES = {
    "english": "English",
    "french": "French",
    "german": "German",
    "italian": "Italian",
    "portuguese": "Brazilian Portuguese",
    "spanish": "Spanish",
}


def _config_names():
    """The model configs the installed package actually ships, by name.

    Read off disk rather than hardcoded, because which languages have a small
    model is not a fact about Pocket TTS, it is a fact about this release of
    it: french ships only as french_24l today, and a later version that adds
    the small one should be picked up without anybody editing this file.

    Cheap -- a find_spec and a directory listing, no import of torch.
    """
    if _CONFIGS["names"] is None:
        import importlib.util

        names = set()
        try:
            spec = importlib.util.find_spec("pocket_tts")
            for where in (spec.submodule_search_locations or []):
                d = os.path.join(where, "config")
                if os.path.isdir(d):
                    names.update(f[:-5] for f in os.listdir(d) if f.endswith(".yaml"))
        except (ImportError, ValueError, OSError):
            pass
        _CONFIGS["names"] = names
    return _CONFIGS["names"]


_CONFIGS = {"names": None}


def model_for(language):
    """What to actually hand load_model for a language.

    Usually the language itself. For French it is `french_24l`, because that is
    the only French model there is -- asking for "french" fails with a message
    telling you so, which is a fine error to read once and a poor one to hit in
    the middle of an answer.

    The 24-layer models are the bigger, slower, better ones. Taking one only
    when there is no alternative keeps the speed where the choice exists.
    """
    lang = (language or DEFAULT_LANGUAGE).strip().lower()
    names = _config_names()
    if not names or lang in names:
        return lang
    wide = f"{lang}_24l"
    return wide if wide in names else lang


def available():
    """Is the package installed, without paying to import it.

    The panel and the CLI both want to know whether to offer this engine at
    all, and they want to know it in a dropdown's worth of time. Importing
    pocket_tts pulls torch in, which is seconds -- so this asks the import
    machinery where the module is and does not load it.
    """
    import importlib.util

    try:
        return importlib.util.find_spec("pocket_tts") is not None
    except (ImportError, ValueError):
        return False


def catalog(language=DEFAULT_LANGUAGE):
    """The built-in voices, shaped like voice_lib.catalog's rows.

    Same keys, so the panel's dropdown, the CLI's listing and the /state
    payload do not have to know which engine they are looking at. `dir` is None
    because these are not folders -- they are states the model fetches by name.

    Cheap on purpose: no import of the package, no network, no model. This is
    called on every panel poll.
    """
    rows = []
    for vid, sex, style, lang in VOICES:
        rows.append({
            "id": vid,
            "name": vid.replace("_", " ").title(),
            "sex": sex,
            "style": style,
            "culture": lang,
            # What goes in the brackets ahead of the sex. Empty for the
            # language being listed first, because saying "english" against
            # twenty-one of them is noise; the five that need a different model
            # wear their language, which is the thing worth knowing about them.
            "tag": "" if lang == language else LANGUAGE_NAMES.get(lang, lang.title()),
            "persona": "",
            "dir": None,
            "root": None,
            "embedding": None,
            "icl": None,
            "pocket": vid,
            "pocketLanguage": lang,
        })
    # The chosen language first, then the rest. Not filtered: hiding a voice
    # behind a config key nobody knows to edit is how Estelle stayed invisible.
    order = {lang: i for i, lang in enumerate(LANGUAGES)}
    rows.sort(key=lambda r: (r["culture"] != language,
                             order.get(r["culture"], 99), r["id"]))
    return rows


def sex_of(voice_id):
    """female/male for a built-in voice, or None for one we do not know."""
    needle = (voice_id or "").lower()
    return next((sex for vid, sex, _, _ in VOICES if vid == needle), None)


def style_of(voice_id):
    """reading/conversation for a built-in voice, or "" where it is not known."""
    needle = (voice_id or "").lower()
    return next((style for vid, _, style, _ in VOICES if vid == needle), "")


def language_of(voice_id):
    """Which model a built-in voice needs, or None for a voice from a file.

    Each language is its own model, and a speaker state is precomputed against
    one of them -- so the voice decides which model is loaded, rather than a
    setting decided hours earlier. That is why picking Estelle works at all.
    """
    needle = (voice_id or "").lower()
    return next((lang for vid, _, _, lang in VOICES if vid == needle), None)


def default_voice(language=DEFAULT_LANGUAGE):
    """Whoever should speak when this engine is chosen and nobody said who."""
    rows = catalog(language)
    if not rows:
        return DEFAULT_VOICE
    return next((r["id"] for r in rows if r["id"] == DEFAULT_VOICE), rows[0]["id"])


class Engine:
    """Held to the same shape as qwen_engine.Engine, and no wider.

    Constructed, then load_models(), then synthesize_streaming() or
    synthesize(). The server's engine thread does exactly that and nothing
    else, so anything added here that it does not call is dead weight.
    """

    def __init__(self, language=None, quantize=False, verbose=False, log=None):
        self.language = language or DEFAULT_LANGUAGE
        self.quantize = bool(quantize)
        self.verbose = verbose
        self._log_to = log
        self.model = None
        # Turning a voice into a model state costs about two seconds the first
        # time -- a small download, then a load. Nobody changes voice between
        # sentences, so one entry is usually the whole cache, but keeping it a
        # dict means flipping back and forth in the panel is free after the
        # first visit.
        self._states = {}

    # -- plumbing ----------------------------------------------------------
    def _log(self, msg):
        if self._log_to:
            self._log_to(msg)
        elif self.verbose:
            print(msg, flush=True)

    def load_models(self, *_args, **_kwargs):
        """Load the model for the configured language.

        Takes and ignores positional arguments so the server can call it the
        same way for either engine; Qwen's wants a model directory and a talker
        filename, and this one gets both of those from the package.
        """
        from pocket_tts import TTSModel                   # imports torch: seconds

        name = model_for(self.language)
        self._log(f"loading pocket-tts ({name}"
                  + (", int8" if self.quantize else "") + ")")
        self.model = TTSModel.load_model(language=name, quantize=self.quantize)
        rate = getattr(self.model, "sample_rate", SAMPLE_RATE)
        if rate != SAMPLE_RATE:
            # Not survivable quietly. Every silence window, the derail guard
            # and the wav header downstream are written against 24000, and a
            # model at some other rate would play at the wrong speed rather
            # than fail -- which is the worst way for this to go wrong.
            raise RuntimeError(
                f"pocket-tts reports {rate} Hz, but this tool is built around "
                f"{SAMPLE_RATE}. Resample in pocket_engine before going further.")
        return self

    def ensure_language(self, language):
        """Load the model this voice belongs to, if it is not the one loaded.

        Six languages means six models, and a speaker state belongs to exactly
        one of them -- hand Estelle's state to the English model and you get
        nonsense rather than an error. So the voice decides the model.

        Costs a few seconds, once, the first time a language is used in a
        session; after that the weights are on disk and it is a load. The
        cached states go with the old model, because they mean nothing to the
        new one.
        """
        if not language or language == self.language:
            return
        if self.model is None:
            self.language = language
            return
        self._log(f"{language} voice on the {self.language} model -- reloading")
        # Held, not dropped. A load that fails -- no network, a language with
        # no model, a gated download -- used to leave this engine with no model
        # at all, and every answer afterwards said "load_models() first" rather
        # than naming what went wrong. Keeping the old one means a failed swap
        # costs the voice you asked for, not the ability to speak.
        was = (self.model, self.language, self._states)
        self.model, self._states = None, {}
        self.language = language
        try:
            self.load_models()
        except Exception:
            self.model, self.language, self._states = was
            raise

    def voice_state(self, voice=None, language=None):
        """The model state for a voice: a built-in name, a wav, or a safetensors.

        A name goes to Kyutai's catalogue. Anything with a path separator or a
        file extension is treated as a file, which is how a cloned voice
        arrives -- get_state_for_audio_prompt takes a plain wav and exports to
        safetensors, so a voice cloned once need not be re-derived after.

        `language` is which model the state belongs to. It is passed in rather
        than looked up because a cloned voice's language is not knowable from
        here -- it is written in that voice's own voice.json, and voice_lib
        reads it there.
        """
        self.ensure_language(language or language_of(voice))
        if self.model is None:
            raise RuntimeError("load_models() first")
        key = voice or default_voice(self.language)
        if key in self._states:
            return self._states[key]

        target = key
        if os.sep in key or "/" in key or key.lower().endswith((".wav", ".safetensors", ".mp3")):
            target = os.path.abspath(key) if os.path.exists(key) else key
        elif sex_of(key) is None:
            raise LookupError(
                f"'{key}' is not a pocket-tts voice. Known: "
                + ", ".join(v for v, _, _, _ in VOICES))

        self._states[key] = self.model.get_state_for_audio_prompt(target)
        return self._states[key]

    # -- what the server calls ---------------------------------------------
    def synthesize_streaming(self, text, on_piece, pocket_voice=None,
                             pocket_language=None, max_seconds=None, **_ignored):
        """One generation, handed over in pieces as it is made.

        `on_piece(samples, None)` is called for each piece, on this thread.
        Return False from it to stop early -- the generator is simply abandoned,
        which is the whole of what stopping costs here. The second argument is
        always None: Qwen's road passes its chunk struct there and the server
        does not look at it.

        The pieces are small -- about 80 ms each, against Qwen's second -- so
        the server's own buffering does more of the work here, and does it
        well: it cuts playback on silence, and eighty-millisecond pieces give
        it more places to cut rather than fewer.

        Returns how many samples were handed over.

        Extra keyword arguments are swallowed on purpose. voice_lib hands the
        server whatever the chosen voice needs, and a Qwen kwarg arriving here
        means the config was changed under a running engine -- better to speak
        in the default voice than to raise at the top of an answer.
        """
        state = self.voice_state(pocket_voice, pocket_language)
        # The same guard Qwen's road keeps, for the same reason: a derail hands
        # back minutes of babbling and reports success. Counting what actually
        # arrives trusts nothing about the model.
        budget = int((max_seconds or 327.68) * SAMPLE_RATE * 1.1) + SAMPLE_RATE
        total = 0
        for piece in self.model.generate_audio_stream(state, text):
            samples = piece.detach().flatten().float().numpy()
            if not samples.size:
                continue
            total += int(samples.size)
            if on_piece(samples, None) is False:
                break
            if total > budget:
                self._log(f"derail guard: {total} samples for {len(text)} "
                          f"characters -- stopping it here")
                break
        return total

    def synthesize(self, text, pocket_voice=None, pocket_language=None,
                   **_ignored):
        """Speak `text` whole. Mono float samples at SAMPLE_RATE."""
        state = self.voice_state(pocket_voice, pocket_language)
        audio = self.model.generate_audio(state, text)
        return audio.detach().flatten().float().numpy()

    def close(self):
        """Drop the model and the cached speaker states.

        The collect is not superstition. These are torch tensors held through a
        few layers of Python object, and the engine is swapped precisely when
        somebody wants the other model's memory back -- waiting for a
        collection that happens whenever means the two overlap for a while, on
        the machine that was short of room enough to ask.
        """
        import gc

        self.model = None
        self._states.clear()
        gc.collect()
