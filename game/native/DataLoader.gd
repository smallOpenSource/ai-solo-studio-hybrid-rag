extends RefCounted
class_name DataLoader
## US-8 — loads + schema-validates the 6 game/data JSON files bundled into the
## exported client at res://data/.
##
## The 6 files are bundled at build time (scripts/build_windows.sh copies the
## whole game/data/ dir -> game/native/data/), so the exported .exe/.pck is
## self-contained. This loader reads them back at runtime, parses each, and runs
## GameSchema.validate_all (the same rule set as office/game_schema.py).
##
## On ANY problem (missing file / bad JSON / schema violation) load() returns a
## result whose .ok is false and whose .errors list is non-empty, so the caller
## surfaces an explicit error instead of silently starting an empty game
## (AC: "한 파일이라도 누락/스키마 위반이면 명시 에러").

const DATA_DIR: String = "res://data/"
const FILE_NAMES: Array[String] = [
	"meta", "enemies", "weapons", "waves", "upgrades", "balance",
]

var ok: bool = false
var errors: PackedStringArray = PackedStringArray()
## file logical name -> parsed Variant (Dictionary for these files).
var bundle: Dictionary = {}


## Loads + validates all 6 files. Returns self for chaining; inspect .ok/.errors.
func load() -> DataLoader:
	errors = PackedStringArray()
	bundle = {}

	for name: String in FILE_NAMES:
		var path: String = "%s%s.json" % [DATA_DIR, name]
		if not FileAccess.file_exists(path):
			errors.append("%s.json: 파일이 없음 (%s)" % [name, path])
			continue
		var f: FileAccess = FileAccess.open(path, FileAccess.READ)
		if f == null:
			errors.append("%s.json: 열기 실패 (err %d)" % [name, FileAccess.get_open_error()])
			continue
		var text: String = f.get_as_text()
		f.close()
		var json: JSON = JSON.new()
		var parse_err: int = json.parse(text)
		if parse_err != OK:
			errors.append("%s.json: JSON 파싱 실패 (line %d: %s)" % [name, json.get_error_line(), json.get_error_message()])
			continue
		bundle[name] = json.data

	# If a file failed to load/parse we already have errors; still run the
	# schema pass on whatever loaded so the report is complete in one shot.
	var schema_res: Dictionary = GameSchema.validate_all(bundle)
	if not bool(schema_res["ok"]):
		for e in (schema_res["errors"] as PackedStringArray):
			errors.append(e)

	ok = errors.is_empty() and bundle.size() == FILE_NAMES.size()
	return self
