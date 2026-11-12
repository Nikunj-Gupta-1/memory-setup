from __future__ import annotations

from memory_setup.vision.classifier import classify_description, is_homework_or_document


def test_faces_always_disqualify_homework_flag() -> None:
    desc = "Two students holding a math notebook and smiling in front of a whiteboard"
    assert is_homework_or_document(desc, has_faces=True) is False


def test_celebration_and_social_scenes_disqualified() -> None:
    assert is_homework_or_document("A birthday cake with lit candles on a dining table near a napkin", has_faces=False) is False
    assert is_homework_or_document("People gathered inside a church altar for a wedding ceremony", has_faces=False) is False
    assert is_homework_or_document("Paper menu resting beside a coffee cup and plate in a cafe", has_faces=False) is False


def test_true_homework_and_code_screens_flagged() -> None:
    assert is_homework_or_document("A page from a notebook showing calculus math equations and integrals", has_faces=False) is True
    assert is_homework_or_document("A screenshot of python source code in a dark editor terminal", has_faces=False) is True
    assert is_homework_or_document("A printed physics worksheet with multiple choice questions", has_faces=False) is True


def test_classify_description_extracts_scene_tags() -> None:
    meta = {"media_id": 1, "city": "Dubai", "country": "United Arab Emirates", "date_taken": "2024-06-15"}
    out = classify_description("A sunset over the desert sand dunes with orange sky", meta, has_faces=False)
    assert out["scene_type"] == "outdoor"
    assert out["is_document_or_homework"] == 0
    assert "outdoor" in out["tags"]
