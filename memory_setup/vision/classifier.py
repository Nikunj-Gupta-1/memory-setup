from __future__ import annotations

import json
from typing import Any, Dict

HIGH_PRECISION_STUDY_PHRASES = (
    "page from a notebook",
    "notebook open to a page",
    "open notebook",
    "page of a notebook",
    "math problem",
    "mathematical equations",
    "math equations",
    "handwritten notes",
    "worksheet",
    "textbook page",
    "page from a textbook",
    "exam paper",
    "question paper",
    "homework assignment",
    "graph paper",
)

PERSON_STARTERS = (
    "in the image, a young boy",
    "in the image, a young girl",
    "in the image, a young woman",
    "in the image, a young man",
    "in the image, a man",
    "in the image, a woman",
    "in the image, a baby",
    "in the image, a child",
    "in the image, a toddler",
    "in the image, a couple",
    "in the image, a group of people",
    "the image depicts a couple",
    "the image depicts a group",
    "the image shows a group of people",
    "the image shows a man and",
    "the image shows a woman and",
    "the image shows a family",
)

PERSONAL_EVENT_EXCLUSIONS = (
    "birthday cake",
    "cake with",
    "standing in front of",
    "mountain range",
    "church",
    "park",
    "beach",
    "temple",
    "shrine",
    "monument",
    "wedding",
)

SUBJECT_MAP = {
    "child": ("child", "kid", "boy", "girl", "baby", "toddler"),
    "outdoor": (
        "park", "tree", "trees", "nature", "sky", "mountain",
        "water", "river", "lake", "canal", "grass", "garden", "beach",
    ),
    "indoor": ("room", "desk", "table", "chair", "bed", "sofa", "wall", "living room", "kitchen", "hall"),
    "travel": ("temple", "shrine", "monument", "landmark", "boat", "street", "city", "building", "historic", "architecture"),
    "food": ("food", "meal", "dinner", "lunch", "cake", "plate", "dish", "restaurant", "tea", "cup"),
}


def is_homework_or_document(desc: str, has_faces: bool = False) -> bool:
    """
    High-precision multi-layer classifier for study documents, notebooks, screenshots, and receipts.
    Strictly disqualifies personal family photos and event settings.
    """
    if has_faces:
        return False

    d = (desc or "").lower().strip()
    if not d:
        return False

    if any(d.startswith(starter) for starter in PERSON_STARTERS):
        return False

    if any(ex in d for ex in PERSONAL_EVENT_EXCLUSIONS):
        return False

    if any(phrase in d for phrase in HIGH_PRECISION_STUDY_PHRASES):
        return True

    if "phone screen displaying" in d or "screenshot of" in d or "screen displaying" in d:
        if not any(
            p in d
            for p in (
                "holding a phone to take a photo",
                "taking a photo with",
                "person holding a phone",
            )
        ):
            return True

    if ("receipt" in d or "invoice" in d) and any(w in d for w in ("price", "total", "tax", "store", "bill")):
        if not any(w in d for w in ("sari", "baby", "birthday", "cake")):
            return True

    return False


def classify_description(
    desc: str,
    item_meta: Dict[str, Any],
    has_faces: bool = False,
) -> Dict[str, Any]:
    desc_lower = desc.lower()
    is_doc = is_homework_or_document(desc, has_faces=has_faces)

    sentences = [s.strip() for s in desc.split(".") if s.strip()]
    short_caption = (sentences[0] + ".") if sentences else desc[:120]

    tags = set()
    if item_meta.get("city"):
        tags.add(str(item_meta["city"]).lower())
    if item_meta.get("country"):
        tags.add(str(item_meta["country"]).lower())

    if is_doc:
        tags.update(["document", "homework", "notes", "study"])
        if any(m in desc_lower for m in ("math", "equation", "calculation", "formula")):
            tags.add("math")
        if any(s in desc_lower for s in ("screenshot", "screen", "phone", "app")):
            tags.add("screenshot")

    if has_faces:
        tags.add("people")

    primary_subjects = set()
    for tag_name, keywords in SUBJECT_MAP.items():
        if any(k in desc_lower for k in keywords):
            tags.add(tag_name)
            primary_subjects.add(tag_name)

    if is_doc:
        scene_type = "document/study"
    elif "outdoor" in tags or "travel" in tags:
        scene_type = "outdoor"
    elif "indoor" in tags:
        scene_type = "indoor"
    elif has_faces:
        scene_type = "portrait"
    else:
        scene_type = "general"

    return {
        "short_caption": short_caption,
        "detailed_description": desc,
        "tags": json.dumps(sorted(list(tags))),
        "scene_type": scene_type,
        "primary_subjects": json.dumps(sorted(list(primary_subjects))),
        "is_document_or_homework": 1 if is_doc else 0,
        "detected_text": None,
    }
