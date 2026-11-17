"""Curly quotes in the model's text: this site shows its briefings with them (wcs.rag.checks turns them on; the
functions are the shared package's, wts_core.checks)."""

from wts_core.checks import curl_answer, curl_events, curl_quotes


def test_quotes_and_apostrophes_are_curled():
    assert curl_quotes('"She said \'no\' to them," he wrote.') == "“She said ‘no’ to them,” he wrote."
    assert curl_quotes("Don't touch the States' rights of the '90s.") == "Don’t touch the States’ rights of the ’90s."
    assert curl_quotes('He said: "We\'ll see."') == "He said: “We’ll see.”"
    assert curl_quotes('"\'Nested\'"') == "“‘Nested’”"
    assert curl_quotes('("peace") — "security"') == "(“peace”) — “security”"
    # the asterisks around bold text are not part of the sentence
    assert curl_quotes('**"Sovereignty"** and **Kenya\'s** view') == "**“Sovereignty”** and **Kenya’s** view"
    assert curl_quotes("No quotes here.") == "No quotes here."


def test_addresses_code_and_tags_are_left_alone():
    text = 'See https://example.org/?q="a" and `x = "b"` and <a href="c">it</a>, "said" it.'
    assert curl_quotes(text) == 'See https://example.org/?q="a" and `x = "b"` and <a href="c">it</a>, “said” it.'


def test_a_quotation_across_segments_and_marks_stay_in_place():
    answer = {"markdown": 'Kenya said "climate finance is slow" and it\'s claimed.',
              "segments": [{"text": 'Kenya said "climate ', "status": "connective"},
                           {"text": 'finance is slow"', "status": "cited", "sources": [1]},
                           {"text": " and it's claimed.", "status": "unsupported",
                            "loaded": [{"start": 10, "end": 17, "word": "claimed"}]}]}
    out = curl_answer(answer)
    assert [s["text"] for s in out["segments"]] == ["Kenya said “climate ", "finance is slow”", " and it’s claimed."]
    seg = out["segments"][2]
    assert seg["text"][10:17] == "claimed" and seg["loaded"] == answer["segments"][2]["loaded"]
    assert out["markdown"] == "Kenya said “climate finance is slow” and it’s claimed."
    assert answer["segments"][0]["text"] == 'Kenya said "climate '  # the original is not changed
    assert curl_answer(None) is None and curl_answer({"segments": []}) == {"segments": []}


def test_events_curl_the_interpretation_and_the_briefing_only():
    events = [{"type": "plan", "plan": {"interpretation": 'Read as "climate finance".'}},
              {"type": "sources", "sources": [{"text": 'He said "no".'}]},
              {"type": "final", "answer": {"segments": [{"text": '"Yes."'}]}}]
    out = curl_events(events)
    assert out[0]["plan"]["interpretation"] == "Read as “climate finance”."
    assert out[1] == events[1]  # the record is quoted as it was printed
    assert out[2]["answer"]["segments"][0]["text"] == "“Yes.”"
