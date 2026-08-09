# AI-assisted glyph review

The decoder can use an OpenAI vision-capable model to rank possible Unicode mappings for unresolved embedded Bangla glyphs.

## Install

```bat
python -m pip install -r requirements.txt
```

Set the API key in the current Windows Command Prompt session:

```bat
set OPENAI_API_KEY=YOUR_API_KEY
```

Optional model override:

```bat
set OPENAI_MODEL=gpt-5.4-mini
```

## Run

AI review automatically enters an interactive confirmation loop:

```bat
python decoder.py 261694_com_1267_female_without_photo_71_2025-11-24.pdf --page 4 --ai-review
```

For one unresolved CID/GID only:

```bat
python decoder.py 261694_com_1267_female_without_photo_71_2025-11-24.pdf --page 4 --ai-review --gid 216
```

For the existing deterministic human review without AI:

```bat
python decoder.py 261694_com_1267_female_without_photo_71_2025-11-24.pdf --page 4 --review
```

## Safety model

AI suggestions are **never written automatically**. For every glyph the program displays the AI candidates and asks for an explicit choice. Choosing a candidate or entering Unicode manually records the mapping with `confidence=1.0` and a human-confirmed source. `N`/`S` skips the glyph and `Q` exits without accepting it.

The AI's confidence is only advisory; it is not treated as mathematical certainty. The persistent learned database remains the authority after a human confirmation.

The API key is read from `OPENAI_API_KEY` and is not stored in the repository or in `learned_glyph_map.json`.
