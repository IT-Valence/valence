"""Text clean-up shared by Master Label and the drum labels.

Everything printed on a drum label goes through these helpers so that what
users type ("bp", " php005726 ") always prints the same way ("BP", "PHP005726").
"""
import re

GRADES = ("IP", "BP", "USP", "EP", "IH", "NA")

# what people actually write -> grade code used on labels
_GRADE_ALIASES = {
	"IP": "IP",
	"BP": "BP",
	"USP": "USP",
	"EP": "EP",
	"PH EUR": "EP",
	"PH.EUR": "EP",
	"PH. EUR.": "EP",
	"IH": "IH",
	"IN-HOUSE": "IH",
	"IN HOUSE": "IH",
	"INHOUSE": "IH",
	"OTHERS/IH": "IH",
	"OTHERS": "IH",
	"NA": "NA",
	"N/A": "NA",
	"": "NA",
}

_LABEL_NO = re.compile(r"^L\s*0*(\d{1,3})\s*-\s*(\d{1,2})$")


def clean_text(value):
	"""Trim and collapse spaces; keep None as None."""
	if value is None:
		return None
	return re.sub(r"\s+", " ", str(value)).strip()


def upper_text(value):
	"""Trimmed, single-spaced, CAPITALS. Used for anything printed on a label."""
	value = clean_text(value)
	return value.upper() if value else value


def normalize_grade(value):
	"""Map free-typed grades to one of GRADES. Returns None if it can't be mapped."""
	key = (clean_text(value) or "").upper()
	key = re.sub(r"\(.*?\)", "", key).strip()  # drop "(With Neutral code)" etc.
	if key in _GRADE_ALIASES:
		return _GRADE_ALIASES[key]
	for alias, grade in _GRADE_ALIASES.items():
		if alias and key.startswith(alias):
			return grade
	return None


def normalize_label_no(value):
	"""'l0041-00' / 'L041-00' / 'L 41 - 0' -> 'L041-00'. Returns None if not a label number."""
	text = (clean_text(value) or "").upper()
	match = _LABEL_NO.match(text)
	if not match:
		return None
	return f"L{int(match.group(1)):03d}-{int(match.group(2)):02d}"


def letters_only(value):
	"""For comparing product names that differ only in spacing/case/punctuation."""
	return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


# grade / variant words that item names and register entries add to a product name
_NAME_NOISE = re.compile(r"\b(IP|BP|USP|EP|NF|IH|NA|PH\.?\s*EUR\.?|IN-?HOUSE|NEUTRAL|WITH NEUTRAL CODE)\b|\(.*?\)")


def product_key(name):
	"""'PHENYLEPHRINE HYDROCHLORIDE USP' / 'PhenylephrineHydrochloride' -> 'PHENYLEPHRINEHYDROCHLORIDE'.

	Lets one product's several ERP items (general item, one per grade) and the
	register's spelling all point at the same master labels.
	"""
	return letters_only(_NAME_NOISE.sub(" ", (name or "").upper()))


def product_similarity(a, b):
	"""0..1: how sure we are two product keys are the same product."""
	import difflib

	if not a or not b:
		return 0
	if a == b:
		return 1
	short, long = sorted((a, b), key=len)
	# 'URSODEOXYCHOLICACID' in 'URSODIOLURSODEOXYCHOLICACID' is the same product, but
	# 'KETOPROFEN' in 'DEXKETOPROFENTROMETAMOL' is not: the contained name must be most of the other.
	if len(short) >= 8 and short in long and len(short) >= 0.6 * len(long):
		return 0.95
	return difflib.SequenceMatcher(None, a, b).ratio()


def same_product(a, b, threshold=0.86):
	return product_similarity(product_key(a), product_key(b)) >= threshold
