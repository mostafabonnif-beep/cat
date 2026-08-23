import json

from scripts import adjust_subtitles


def _generate(tmp_path, animation="pop_scale", auto_emoji=True):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "captions.json"
    output = project / "captions.ass"
    source.write_text(json.dumps({
        "segments": [{
            "words": [
                {"word": "Wow", "start": 0.0, "end": 0.6},
                {"word": "secret", "start": 0.7, "end": 1.3},
            ]
        }]
    }), encoding="utf-8")
    adjust_subtitles.generate_ass_from_file(
        str(source), str(output), str(project),
        "&H00FFFFFF&", 30, 36, "&H0000FF00&", 2, 0.1, "highlight",
        210, 2, "Montserrat-Regular", "&H00000000&", "&H00000000&",
        0, 0, 0, 0, 1, 1.5, 2, 0, {}, True, animation, auto_emoji,
    )
    return output.read_text(encoding="utf-8")


def test_dynamic_captions_add_ass_animation_tags(tmp_path):
    content = _generate(tmp_path, animation="pop_scale", auto_emoji=False)
    assert "\\t(" in content
    assert "\\fscx" in content


def test_auto_emoji_decorates_supported_words(tmp_path):
    content = _generate(tmp_path, animation="none", auto_emoji=True)
    assert "🤯" in content
    assert "🤫" in content


def test_emoji_helper_is_conservative():
    assert adjust_subtitles.emoji_for_word("wow") == "🤯"
    assert adjust_subtitles.emoji_for_word("ordinary") == ""
