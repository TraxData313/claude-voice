# Languages

The voices were cloned from English, but neither the model nor anything in this project is
tied to it. Nothing between the answer and the speaker cares which alphabet it is handed.

| | |
|---|---|
| <img src="flags/us.png" height="20" alt="US"> **English** | tested — both shipped voices were cloned from it |
| <img src="flags/ru.png" height="20" alt="RU"> **Russian** | tested — Abby reads it genuinely well |
| <img src="flags/bg.png" height="20" alt="BG"> **Bulgarian** | reads, but in a **Russian accent** |
| 🌐 **most others** | should read normally — untested, so try one |

## Trying one

```powershell
python voice_cli.py say "Сейчас я проверю, как это звучит."
```

Or simply write a line in that language and let the answer be spoken. There is no setting to
change: the language is whatever the text is.

## Two things to expect

**A voice keeps the accent of the recording it was cloned from.** Abby learned her sound
from English speech, so everything she says arrives coloured by it. In Russian that turns
out charming. It may not always be what you wanted.

**A smaller language can be mistaken for its bigger neighbour.** Bulgarian is the clearest
case found so far: the words come out right and fluent, but spoken as though they were
Russian, because the model recognises the alphabet before it recognises the language. Expect
that shape of failure rather than gibberish.

## Not yet walked

A language written **right to left**, or one with **no spaces between words**, is territory
nobody here has tried. The chunker splits on sentence punctuation and spaces, so a script
that uses neither may be handed to the model in unhelpful pieces. If it comes out wrong,
that is the model or the chunking — not a setting you have missed.

Found one that works, or one that does not? Say so in an issue, and it goes in the table.

## A note on the fix behind this

Until recently every one of these would have been silent. Two filters asked whether a line
contained `[A-Za-z0-9]` before deciding it was worth speaking, so a line written entirely in
Cyrillic, Greek or Chinese was discarded exactly like punctuation — without a word in the
log. The test is now for a letter or digit in **any** script. If you add a language and hear
nothing at all, that class of bug is worth suspecting first.
