extends RefCounted
class_name GameSchema
## US-8 — Godot port of the game data schema validator.
##
## Ports the SAME validation rules as office/game_schema.py and game/src/schema.ts
## (the 3rd parallel port). Each validator returns a Dictionary:
##   { "ok": bool, "errors": PackedStringArray }
## so a single missing field or out-of-range value is surfaced explicitly
## (AC: "한 파일이라도 누락/스키마 위반이면 명시 에러").
##
## Rules kept equivalent to the Python/TS originals:
##   - numeric ranges (>0 vs >=0 via `exclusive`), enum membership, min count,
##     non-decreasing curves, waves.tStart strictly increasing,
##   - bool must NOT be accepted where a number is required,
##   - cross-file referential integrity: waves.spawn.enemyId ∈ enemies.id
##     (only in validate_all, matching the originals).
##
## NOTE: strictly typed (no Variant inference) — Godot 4.3 treats inference
## warnings as errors, which would leave the exported script uncompiled
## (US-7 lesson). JSON values arrive as Variant from JSON.parse_string; this
## file is the ONE place Variant is unavoidable, and every branch casts via
## `is Type` before use.

const ENEMY_BEHAVIORS: Array[String] = ["chase", "swarm", "ranged", "charger"]


# ── numeric helpers (mirror _is_number / _is_int; bool is NOT a number) ──────

static func _is_number(v: Variant) -> bool:
	# JSON numbers parse to FLOAT (and INT for some). bool must be rejected:
	# TYPE_BOOL is distinct in Godot, so a plain type check already excludes it.
	var t: int = typeof(v)
	return t == TYPE_INT or t == TYPE_FLOAT


static func _is_int(v: Variant) -> bool:
	var t: int = typeof(v)
	if t == TYPE_INT:
		return true
	if t == TYPE_FLOAT:
		var f: float = v
		return f == floor(f) and not is_inf(f) and not is_nan(f)
	return false


static func _check_str(obj: Dictionary, key: String, where: String, errors: Array) -> void:
	if not obj.has(key):
		errors.append("%s: 필수 필드 누락 '%s'" % [where, key])
		return
	var val: Variant = obj[key]
	if typeof(val) != TYPE_STRING or (val as String).is_empty():
		errors.append("%s: '%s'는 비어있지 않은 문자열이어야 함" % [where, key])


static func _check_number(
	obj: Dictionary,
	key: String,
	where: String,
	errors: Array,
	minimum: Variant = null,
	exclusive: bool = false,
	integer: bool = false,
) -> void:
	if not obj.has(key):
		errors.append("%s: 필수 필드 누락 '%s'" % [where, key])
		return
	var val: Variant = obj[key]
	if integer:
		if not _is_int(val):
			errors.append("%s: '%s'는 정수여야 함" % [where, key])
			return
	elif not _is_number(val):
		errors.append("%s: '%s'는 숫자여야 함" % [where, key])
		return
	if minimum != null:
		var num: float = float(val)
		var lo: float = float(minimum)
		if exclusive and not (num > lo):
			errors.append("%s: '%s'는 %s보다 커야 함 (받음: %s)" % [where, key, str(minimum), str(val)])
		elif not exclusive and not (num >= lo):
			errors.append("%s: '%s'는 %s 이상이어야 함 (받음: %s)" % [where, key, str(minimum), str(val)])


static func _check_number_list(
	obj: Dictionary,
	key: String,
	where: String,
	errors: Array,
	non_decreasing: bool = false,
) -> void:
	if not obj.has(key):
		errors.append("%s: 필수 필드 누락 '%s'" % [where, key])
		return
	var arr: Variant = obj[key]
	if typeof(arr) != TYPE_ARRAY:
		errors.append("%s: '%s'는 배열이어야 함" % [where, key])
		return
	var list: Array = arr
	for x in list:
		if not _is_number(x):
			errors.append("%s: '%s' 원소는 모두 숫자여야 함" % [where, key])
			return
	if non_decreasing:
		for i in range(1, list.size()):
			if float(list[i]) < float(list[i - 1]):
				errors.append(
					"%s: '%s'는 비감소여야 함 (index %d: %s < %s)"
					% [where, key, i, str(list[i]), str(list[i - 1])]
				)
				break


static func _items_list(
	obj: Variant, key: String, min_count: int, label: String, errors: Array
) -> Array:
	if typeof(obj) != TYPE_DICTIONARY:
		errors.append("%s: 루트는 객체여야 함" % label)
		return []
	var rec: Dictionary = obj
	if not rec.has(key) or typeof(rec[key]) != TYPE_ARRAY:
		errors.append("%s: '%s'는 배열이어야 함" % [label, key])
		return []
	var items: Array = rec[key]
	if items.size() < min_count:
		errors.append("%s: '%s'는 최소 %d개여야 함 (받음: %d)" % [label, key, min_count, items.size()])
	return items


static func _result(errors: Array) -> Dictionary:
	var packed: PackedStringArray = PackedStringArray()
	for e in errors:
		packed.append(str(e))
	return {"ok": errors.is_empty(), "errors": packed}


# ── per-file validators (mirror validate_*) ─────────────────────────────────

static func validate_meta(obj: Variant) -> Dictionary:
	var errors: Array = []
	if typeof(obj) != TYPE_DICTIONARY:
		return _result(["meta: 루트는 객체여야 함"])
	var rec: Dictionary = obj
	for key: String in ["theme", "title", "coreLoop", "win", "lose"]:
		_check_str(rec, key, "meta", errors)
	var cont: Variant = rec.get("continue")
	if typeof(cont) != TYPE_DICTIONARY:
		errors.append("meta: 'continue'는 객체여야 함")
	else:
		var c: Dictionary = cont
		_check_number(c, "freeRetries", "meta.continue", errors, 0, false, true)
		_check_number_list(c, "costCurve", "meta.continue", errors, true)
		_check_number(c, "sessionCap", "meta.continue", errors, 1, false, true)
	return _result(errors)


static func validate_enemies(obj: Variant) -> Dictionary:
	var errors: Array = []
	var items: Array = _items_list(obj, "enemies", 3, "enemies", errors)
	var seen_ids: Dictionary = {}
	for i in range(items.size()):
		var e: Variant = items[i]
		var where: String = "enemies[%d]" % i
		if typeof(e) != TYPE_DICTIONARY:
			errors.append("%s: 객체여야 함" % where)
			continue
		var rec: Dictionary = e
		_check_str(rec, "id", where, errors)
		_check_str(rec, "name", where, errors)
		_check_number(rec, "hp", where, errors, 0, true)
		_check_number(rec, "speed", where, errors, 0, true)
		_check_number(rec, "damage", where, errors, 0)
		_check_number(rec, "spawnWeight", where, errors, 0, true)
		var behavior: Variant = rec.get("behavior")
		if not (typeof(behavior) == TYPE_STRING and ENEMY_BEHAVIORS.has(behavior)):
			errors.append("%s: 'behavior'는 %s 중 하나여야 함 (받음: %s)" % [where, str(ENEMY_BEHAVIORS), str(behavior)])
		var eid: Variant = rec.get("id")
		if typeof(eid) == TYPE_STRING and not (eid as String).is_empty():
			if seen_ids.has(eid):
				errors.append("%s: 'id' 중복 (%s)" % [where, str(eid)])
			seen_ids[eid] = true
	return _result(errors)


static func validate_weapons(obj: Variant) -> Dictionary:
	var errors: Array = []
	var items: Array = _items_list(obj, "weapons", 2, "weapons", errors)
	for i in range(items.size()):
		var w: Variant = items[i]
		var where: String = "weapons[%d]" % i
		if typeof(w) != TYPE_DICTIONARY:
			errors.append("%s: 객체여야 함" % where)
			continue
		var rec: Dictionary = w
		_check_str(rec, "id", where, errors)
		_check_str(rec, "name", where, errors)
		_check_number(rec, "fireRate", where, errors, 0, true)
		_check_number(rec, "projectiles", where, errors, 1, false, true)
		_check_number(rec, "damage", where, errors, 0, true)
		_check_number(rec, "range", where, errors, 0, true)
		_check_number(rec, "pierce", where, errors, 0, false, true)
	return _result(errors)


static func validate_waves(obj: Variant) -> Dictionary:
	var errors: Array = []
	var items: Array = _items_list(obj, "waves", 2, "waves", errors)
	var prev_tstart: Variant = null
	for i in range(items.size()):
		var w: Variant = items[i]
		var where: String = "waves[%d]" % i
		if typeof(w) != TYPE_DICTIONARY:
			errors.append("%s: 객체여야 함" % where)
			continue
		var rec: Dictionary = w
		_check_number(rec, "tStart", where, errors, 0)
		_check_number(rec, "difficulty", where, errors, 0, true)
		var t_start: Variant = rec.get("tStart")
		var t_end: Variant = rec.get("tEnd")
		if not _is_number(t_end):
			errors.append("%s: 'tEnd'는 숫자여야 함" % where)
		elif _is_number(t_start) and not (float(t_end) > float(t_start)):
			errors.append("%s: 'tEnd'(%s)는 'tStart'(%s)보다 커야 함" % [where, str(t_end), str(t_start)])
		if _is_number(t_start):
			if prev_tstart != null and not (float(t_start) > float(prev_tstart)):
				errors.append("%s: 'tStart'는 오름차순이어야 함 (%s <= 이전 %s)" % [where, str(t_start), str(prev_tstart)])
			prev_tstart = t_start
		var spawn: Variant = rec.get("spawn")
		if typeof(spawn) != TYPE_ARRAY or (spawn as Array).is_empty():
			errors.append("%s: 'spawn'은 비어있지 않은 배열이어야 함" % where)
		else:
			var spawn_arr: Array = spawn
			for j in range(spawn_arr.size()):
				var s: Variant = spawn_arr[j]
				var swhere: String = "%s.spawn[%d]" % [where, j]
				if typeof(s) != TYPE_DICTIONARY:
					errors.append("%s: 객체여야 함" % swhere)
					continue
				var srec: Dictionary = s
				_check_str(srec, "enemyId", swhere, errors)
				_check_number(srec, "rate", swhere, errors, 0, true)
	return _result(errors)


static func validate_upgrades(obj: Variant) -> Dictionary:
	var errors: Array = []
	var items: Array = _items_list(obj, "upgrades", 3, "upgrades", errors)
	for i in range(items.size()):
		var u: Variant = items[i]
		var where: String = "upgrades[%d]" % i
		if typeof(u) != TYPE_DICTIONARY:
			errors.append("%s: 객체여야 함" % where)
			continue
		var rec: Dictionary = u
		_check_str(rec, "id", where, errors)
		_check_str(rec, "name", where, errors)
		_check_str(rec, "stat", where, errors)
		_check_number(rec, "delta", where, errors)
		_check_number(rec, "cap", where, errors)
	return _result(errors)


static func validate_balance(obj: Variant) -> Dictionary:
	var errors: Array = []
	if typeof(obj) != TYPE_DICTIONARY:
		return _result(["balance: 루트는 객체여야 함"])
	var rec: Dictionary = obj
	_check_number(rec, "survivalTargetSec", "balance", errors, 0, true)
	_check_number_list(rec, "dpsCurve", "balance", errors, true)
	if not rec.has("inRange"):
		errors.append("balance: 필수 필드 누락 'inRange'")
	elif typeof(rec["inRange"]) != TYPE_BOOL:
		errors.append("balance: 'inRange'는 불리언이어야 함")
	var report: Variant = rec.get("report")
	if typeof(report) != TYPE_DICTIONARY:
		errors.append("balance: 'report'는 객체여야 함")
	else:
		var rep: Dictionary = report
		for k: Variant in rep.keys():
			if typeof(rep[k]) != TYPE_BOOL:
				errors.append("balance.report['%s']: 불리언이어야 함" % str(k))
	return _result(errors)


# ── integrated validation: per-file + cross-file referential integrity ──────

## Validates the full 6-file bundle. `bundle` maps the 6 logical file names
## (meta/enemies/weapons/waves/upgrades/balance) to their parsed Variant value.
## Each error is prefixed with "<file>.json → ...", matching the originals.
static func validate_all(bundle: Dictionary) -> Dictionary:
	var errors: Array = []
	# (logical name, Callable) — Callables to the static validators; dynamic
	# string dispatch (callv) is unavailable from a static function.
	var checks: Array = [
		["meta", Callable(GameSchema, "validate_meta")],
		["enemies", Callable(GameSchema, "validate_enemies")],
		["weapons", Callable(GameSchema, "validate_weapons")],
		["waves", Callable(GameSchema, "validate_waves")],
		["upgrades", Callable(GameSchema, "validate_upgrades")],
		["balance", Callable(GameSchema, "validate_balance")],
	]
	for pair: Array in checks:
		var name: String = pair[0]
		var fn: Callable = pair[1]
		var obj: Variant = bundle.get(name)
		var res: Dictionary = fn.call(obj)
		if not bool(res["ok"]):
			for e in (res["errors"] as PackedStringArray):
				errors.append("%s.json → %s" % [name, e])

	# cross-file: waves.spawn.enemyId ∈ enemies.id
	var enemies_data: Variant = bundle.get("enemies")
	var waves_data: Variant = bundle.get("waves")
	if typeof(enemies_data) == TYPE_DICTIONARY and typeof(waves_data) == TYPE_DICTIONARY:
		var enemy_ids: Dictionary = {}
		var elist: Variant = (enemies_data as Dictionary).get("enemies")
		if typeof(elist) == TYPE_ARRAY:
			for e in (elist as Array):
				if typeof(e) == TYPE_DICTIONARY and typeof((e as Dictionary).get("id")) == TYPE_STRING:
					enemy_ids[(e as Dictionary)["id"]] = true
		var wlist: Variant = (waves_data as Dictionary).get("waves")
		if typeof(wlist) == TYPE_ARRAY:
			var warr: Array = wlist
			for i in range(warr.size()):
				if typeof(warr[i]) != TYPE_DICTIONARY:
					continue
				var spawn: Variant = (warr[i] as Dictionary).get("spawn")
				if typeof(spawn) != TYPE_ARRAY:
					continue
				var sarr: Array = spawn
				for j in range(sarr.size()):
					if typeof(sarr[j]) != TYPE_DICTIONARY:
						continue
					var ref: Variant = (sarr[j] as Dictionary).get("enemyId")
					if typeof(ref) == TYPE_STRING and not enemy_ids.has(ref):
						errors.append("참조 무결성: waves[%d].spawn[%d].enemyId=%s가 enemies.id에 없음" % [i, j, str(ref)])

	return _result(errors)
