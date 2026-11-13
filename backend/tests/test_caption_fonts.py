"""Caption fonts, emphasised words in their own font, and fonts on text layers."""

import re

import pytest
from fontTools.ttLib import TTFont
from pydantic import ValidationError

from editor import agent, captions, diff, store, typefaces
from editor.captions import TimedWord
from editor.llm import AssistantTurn
from editor.timeline import Timeline
from test_agent import U, ScriptedProvider, call, client, project, talk_video, tool_results  # noqa: F401


def test_every_font_is_bundled_with_matching_name_and_metrics():
    files = {}
    for path in captions.FONTS_DIR.glob("*.ttf"):
        f = TTFont(path)
        names = {f["name"].getDebugName(4), f["name"].getBestFamilyName()}
        os2 = f["OS/2"]
        for n in names:
            files[n] = round(f["head"].unitsPerEm / (os2.usWinAscent + os2.usWinDescent), 3)
    for fid, meta in typefaces.FONTS.items():
        assert meta["ass"] in files, fid                       # libass can find it by this name
        assert files[meta["ass"]] == pytest.approx(meta["em"], abs=0.002), fid
    licences = {p.name for p in captions.FONTS_DIR.glob("*.txt")}
    assert len(licences) >= 10


def words(text, step=0.4):
    return [TimedWord(w, i * step, i * step + 0.3) for i, w in enumerate(text.split())]


def test_emphasised_words_phrases_and_numbers():
    ws = words("We made $5,000 in 30 days, really long trunks and 50% more. Trunks!")
    emp = Timeline.model_validate({"clips": [{"id": "c", "asset_id": "a", "in": 0, "out": 1}],
                                   "captions": {"emphasis": {"words": ["long trunks", "Trunks", "trunks"]}}}).captions.emphasis
    assert emp.words == ["long trunks", "Trunks"]                 # duplicates dropped, case-insensitively
    got = captions.emphasised(ws, emp)
    assert [ws[i].text for i in sorted(got)] == ["long", "trunks", "Trunks!"]
    emp2 = emp.model_copy(update={"numbers": True, "words": []})
    assert [ws[i].text for i in sorted(captions.emphasised(ws, emp2))] == ["$5,000", "30", "50%"]
    assert captions.emphasised(ws, None) == set()


def test_bad_font_rejected():
    with pytest.raises(ValidationError):
        Timeline.model_validate({"clips": [{"id": "c", "asset_id": "a", "in": 0, "out": 1}], "captions": {"font": "comic_sans"}})


def _ass(caps, text=None):
    tl = Timeline.model_validate({"clips": [{"id": "c1", "asset_id": "a", "in": 0, "out": 6}],
                                  "output": {"width": 1080, "height": 1920}, "captions": caps, "text": text or []})
    t = {"words": [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in
                   enumerate("so the elephants have really long trunks okay".split())],
         "segments": [{"start": 0, "end": 4, "text": "x"}]}
    return captions.build_ass(tl, {"a": t})


def test_ass_uses_fonts_and_scales_sizes():
    plain = _ass({"style": "karaoke"})
    styled = _ass({"style": "karaoke", "font": "pacifico",
                   "emphasis": {"words": ["elephants"], "font": "bangers", "color": "#FF4FA3", "size": 1.5}},
                  [{"id": "t1", "text": "Zoo", "start": 0, "end": 1, "style": "title", "font": "bebas"}])
    size = lambda ass, name: float(re.search(rf"Style: {name},([^,]+),(\d+)", ass).group(2))   # noqa: E731
    assert "Style: Caption,Pacifico," in styled and "Style: Text0,Bebas Neue," in styled
    # Pacifico's tall line height draws small at the same size, so its size is raised by the metric ratio
    assert size(styled, "Caption") == pytest.approx(size(plain, "Caption") * 0.640 / 0.517, rel=0.01)
    assert "{\\fnBangers\\fs" in styled and "ELEPHANTS" in styled
    assert captions.ass_color("#FF4FA3") in styled                    # pink
    e_size = float(re.search(r"\\fnBangers\\fs(\d+)", styled).group(1))
    assert e_size == pytest.approx(size(plain, "Caption") * 0.640 / 0.569 * 1.5, rel=0.02)
    assert "\\fnBangers" not in _ass({"style": "karaoke", "emphasis": {"words": ["giraffes"]}})


def test_agent_sets_fonts_and_emphasis(client, project):
    pid, aid = project
    client.put(f"/v2/projects/{pid}/timeline", params=U,
               json={"timeline": {"clips": [{"id": "c1", "asset_id": aid, "in": 0, "out": 8}]}})
    p = ScriptedProvider([
        AssistantTurn(None, [call("set_captions", style="karaoke", font="poppins", emphasis_words=["video editor"],
                                  emphasis_font="permanent_marker", emphasis_numbers=True)]),
        AssistantTurn(None, [call("set_captions", emphasis_add=["welcome"], emphasis_color="#00E0FF"),
                             call("add_text", text="Hi", start=0, end=1, font="pacifico")]),
        AssistantTurn("Done.", []),
    ])
    agent.run(p, pid, "comic captions with the important words in a marker font", history=[])
    r = tool_results(p)
    assert all(x.get("ok") for x in r[:2]) and r[2]["ok"]
    caps = store.get_timeline(pid)["data"]["captions"]
    assert caps["font"] == "poppins"
    assert caps["emphasis"]["words"] == ["video editor", "welcome"] and caps["emphasis"]["numbers"]
    assert caps["emphasis"]["font"] == "permanent_marker" and caps["emphasis"]["color"] == "#00E0FF"
    assert store.get_timeline(pid)["data"]["text"][0]["font"] == "pacifico"
    first = store.get_timeline(pid, 1)["data"]
    changes = diff.describe_changes(first, store.get_timeline(pid)["data"])
    assert any(c.startswith("Emphasised words in Permanent Marker: video editor, welcome, numbers") for c in changes)

    p2 = ScriptedProvider([AssistantTurn(None, [call("set_captions", font="default", emphasis_off=True)]),
                           AssistantTurn("Done.", [])])
    agent.run(p2, pid, "plain captions again", history=[])
    caps = store.get_timeline(pid)["data"]["captions"]
    assert caps.get("font") is None and caps.get("emphasis") is None


def test_caption_settings_api_and_fonts_list(client, project):
    pid, aid = project
    client.put(f"/v2/projects/{pid}/timeline", params=U,
               json={"timeline": {"clips": [{"id": "c1", "asset_id": aid, "in": 0, "out": 8}], "captions": {}}})
    fonts = client.get("/v2/fonts").json()["fonts"]
    assert [f["id"] for f in fonts] == list(typefaces.FONT_IDS)
    r = client.post(f"/v2/projects/{pid}/captions", params=U,
                    json={"settings": {"font": "bebas", "emphasis": {"words": ["editor"], "font": "bangers"}}})
    assert r.status_code == 200, r.text
    caps = store.get_timeline(pid)["data"]["captions"]
    assert caps["font"] == "bebas" and caps["emphasis"]["words"] == ["editor"]
    r = client.post(f"/v2/projects/{pid}/captions", params=U, json={"settings": {"font": "wingdings"}})
    assert r.status_code == 422
