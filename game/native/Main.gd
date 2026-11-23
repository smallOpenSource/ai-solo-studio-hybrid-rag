extends Node2D
## US-8 — data-driven native client for "Aether Scourge".
##
## On startup it loads ALL SIX game/data files (meta, enemies, weapons, waves,
## upgrades, balance) via DataLoader, schema-validates them (GameSchema — same
## rules as office/game_schema.py), and on success composes a MINIMAL PLAYABLE
## survivor-like scene driven entirely by that data:
##   - player auto-kites away from the nearest enemy (PLAYER_SPEED),
##   - weapons.json drives auto-fire (fireRate / projectiles / range / damage),
##   - waves.json drives enemy spawning over time; enemies.json drives their
##     hp / speed / damage / behavior,
##   - projectile<->enemy and enemy<->player collisions resolve kills / HP loss.
##
## This is an HONEST MINIMAL slice, NOT a reproduction of the full web sim.ts
## (upgrades/leveling, boss, win/lose end states are out of scope here). The
## point is to prove the SAME 6 data files COMPOSE a running game in the native
## client — see report for scope limits.
##
## Headless proof: when DisplayServer is "headless" it prints the load /
## validation / scene-init evidence to stdout (so the exported .exe smoke run is
## observable) and the scene self-terminates via the project's --quit-after.
##
## NOTE: strictly typed (no Variant inference) — Godot 4.3 treats inference
## warnings as errors and would otherwise leave the script uncompiled (US-7).

# ── world / player tunables (mirror sim.ts magnitudes so data scales match) ──
const WORLD_W: float = 960.0
const WORLD_H: float = 640.0
const PLAYER_BASE_HP: float = 120.0
const PLAYER_SPEED: float = 130.0
const PLAYER_RADIUS: float = 12.0
const ENEMY_RADIUS: float = 10.0
const PROJECTILE_SPEED: float = 360.0
const PROJECTILE_RADIUS: float = 6.0
const CONTACT_DAMAGE_COOLDOWN: float = 0.5
const ENTITY_CAP: int = 300  # low-spec guard (mirrors pool.ts ENTITY_CAP)

# parsed + validated data (set in _ready on success).
var _meta: Dictionary = {}
var _enemy_defs: Array = []
var _weapon_defs: Array = []
var _upgrade_defs: Array = []
var _flat_spawns: Array = []  # [{def_index, rate, t_start, t_end}]

# runtime state.
var _data_ok: bool = false
var _headless: bool = false
var _sim_time: float = 0.0
var _sim_tick: int = 0
var _kills: int = 0
var _spawned: int = 0
var _player := {"x": WORLD_W * 0.5, "y": WORLD_H * 0.5, "hp": PLAYER_BASE_HP, "max_hp": PLAYER_BASE_HP}
var _enemies: Array = []       # [{x,y,hp,speed,damage,behavior,cooldown}]
var _projectiles: Array = []   # [{x,y,vx,vy,damage,pierce,range_left}]
var _weapon_cooldowns: PackedFloat32Array = PackedFloat32Array()

# UI labels (windowed mode).
var _hud: Label = null


func _ready() -> void:
	_headless = DisplayServer.get_name() == "headless"

	var loader: DataLoader = DataLoader.new().load()
	_data_ok = loader.ok

	print("[AetherScourge] data-driven native client (Godot ", Engine.get_version_info().get("string", ""), ")")
	print("[AetherScourge] loaded files: ", loader.bundle.keys())
	if _data_ok:
		_meta = loader.bundle["meta"] as Dictionary
		print("[AetherScourge] 6-file load+schema OK")
		print("[AetherScourge] meta.title=", str(_meta.get("title", "?")))
		_ingest(loader.bundle)
		print("[AetherScourge] scene init OK: enemies=", _enemy_defs.size(),
			" weapons=", _weapon_defs.size(), " upgrades=", _upgrade_defs.size(),
			" waves(flatSpawns)=", _flat_spawns.size())
	else:
		# AC: a single missing/invalid file → explicit error (no fake success).
		printerr("[AetherScourge] DATA LOAD FAILED — ", loader.errors.size(), " error(s):")
		for e in loader.errors:
			printerr("  - ", e)

	_build_scene()

	if _headless:
		# Headless smoke proof: report and quit (the project sets --quit-after at
		# the CLI; we also stop processing so nothing spins). This makes the
		# exported .exe's data-load + compile + scene-init observable in logs.
		print("[AetherScourge] headless mode: data-load + scene-init proof printed; awaiting --quit-after")
		if not _data_ok:
			# Signal failure clearly to any CI that greps the log.
			print("[AetherScourge] RESULT: FAIL (data invalid)")
		else:
			print("[AetherScourge] RESULT: OK")
		set_process(false)


# Resolve the validated bundle into typed runtime arrays the loop consumes.
func _ingest(bundle: Dictionary) -> void:
	_enemy_defs = ((bundle["enemies"] as Dictionary)["enemies"]) as Array
	_weapon_defs = ((bundle["weapons"] as Dictionary)["weapons"]) as Array
	_upgrade_defs = ((bundle["upgrades"] as Dictionary)["upgrades"]) as Array

	# index enemies by id for waves cross-reference.
	var idx_by_id: Dictionary = {}
	for i in range(_enemy_defs.size()):
		idx_by_id[(_enemy_defs[i] as Dictionary)["id"]] = i

	# flatten waves.spawn into timed spawn entries (mirror sim.ts flatSpawns).
	_flat_spawns = []
	var waves: Array = ((bundle["waves"] as Dictionary)["waves"]) as Array
	for w: Variant in waves:
		var wave: Dictionary = w
		var t_start: float = float(wave["tStart"])
		var t_end: float = float(wave["tEnd"])
		for s: Variant in (wave["spawn"] as Array):
			var spawn: Dictionary = s
			var enemy_id: String = spawn["enemyId"]
			if not idx_by_id.has(enemy_id):
				continue  # referential integrity is validated; defensive skip
			_flat_spawns.append({
				"def_index": int(idx_by_id[enemy_id]),
				"rate": float(spawn["rate"]),
				"t_start": t_start,
				"t_end": t_end,
				"acc": 0.0,
			})

	# weapon cooldowns start at one period (mirror sim.ts).
	_weapon_cooldowns = PackedFloat32Array()
	for wd: Variant in _weapon_defs:
		_weapon_cooldowns.append(1.0 / float((wd as Dictionary)["fireRate"]))


# ── per-frame data-driven loop (windowed mode) ──────────────────────────────
func _process(delta: float) -> void:
	if not _data_ok or _player["hp"] <= 0.0:
		return
	_tick(delta)
	queue_redraw()
	_update_hud()


# One simulation step (also callable for a deterministic headless smoke if
# desired). Player kites, weapons fire, enemies spawn/move, collisions resolve.
func _tick(dt: float) -> void:
	_sim_time += dt
	_sim_tick += 1
	_spawn_enemies(dt)
	_move_player(dt)
	_move_enemies(dt)
	_fire_weapons(dt)
	_move_projectiles(dt)
	_resolve_hits()
	_resolve_contact()


func _spawn_enemies(dt: float) -> void:
	for fs: Variant in _flat_spawns:
		var entry: Dictionary = fs
		if _sim_time < float(entry["t_start"]) or _sim_time >= float(entry["t_end"]):
			continue
		entry["acc"] = float(entry["acc"]) + float(entry["rate"]) * dt
		while float(entry["acc"]) >= 1.0:
			entry["acc"] = float(entry["acc"]) - 1.0
			if _enemies.size() + _projectiles.size() >= ENTITY_CAP:
				break  # low-spec guard: refuse spawn at cap
			var def: Dictionary = _enemy_defs[int(entry["def_index"])]
			var pt: Vector2 = _edge_spawn_point()
			_enemies.append({
				"x": pt.x, "y": pt.y,
				"hp": float(def["hp"]),
				"speed": float(def["speed"]),
				"damage": float(def["damage"]),
				"behavior": str(def["behavior"]),
				"cooldown": 0.0,
			})
			_spawned += 1


func _edge_spawn_point() -> Vector2:
	# deterministic-enough edge pick (gameplay, not a determinism gate here).
	var side: int = randi() % 4
	match side:
		0: return Vector2(randf() * WORLD_W, 0.0)
		1: return Vector2(WORLD_W, randf() * WORLD_H)
		2: return Vector2(randf() * WORLD_W, WORLD_H)
		_: return Vector2(0.0, randf() * WORLD_H)


func _move_player(dt: float) -> void:
	var px: float = _player["x"]
	var py: float = _player["y"]
	var nearest_d2: float = INF
	var nx: float = 0.0
	var ny: float = 0.0
	for e: Variant in _enemies:
		var en: Dictionary = e
		var d2: float = _dist2(px, py, en["x"], en["y"])
		if d2 < nearest_d2:
			nearest_d2 = d2
			nx = en["x"]
			ny = en["y"]
	var dx: float
	var dy: float
	if is_inf(nearest_d2):
		dx = WORLD_W * 0.5 - px
		dy = WORLD_H * 0.5 - py
	else:
		dx = px - nx + (WORLD_W * 0.5 - px) * 0.2
		dy = py - ny + (WORLD_H * 0.5 - py) * 0.2
	var len: float = sqrt(dx * dx + dy * dy)
	if len == 0.0:
		len = 1.0
	_player["x"] = clampf(px + (dx / len) * PLAYER_SPEED * dt, PLAYER_RADIUS, WORLD_W - PLAYER_RADIUS)
	_player["y"] = clampf(py + (dy / len) * PLAYER_SPEED * dt, PLAYER_RADIUS, WORLD_H - PLAYER_RADIUS)


func _move_enemies(dt: float) -> void:
	var px: float = _player["x"]
	var py: float = _player["y"]
	for e: Variant in _enemies:
		var en: Dictionary = e
		var dx: float = px - float(en["x"])
		var dy: float = py - float(en["y"])
		var len: float = sqrt(dx * dx + dy * dy)
		if len == 0.0:
			len = 1.0
		en["x"] = float(en["x"]) + (dx / len) * float(en["speed"]) * dt
		en["y"] = float(en["y"]) + (dy / len) * float(en["speed"]) * dt
		if float(en["cooldown"]) > 0.0:
			en["cooldown"] = float(en["cooldown"]) - dt


func _fire_weapons(dt: float) -> void:
	var px: float = _player["x"]
	var py: float = _player["y"]
	for wi in range(_weapon_defs.size()):
		var w: Dictionary = _weapon_defs[wi]
		_weapon_cooldowns[wi] = _weapon_cooldowns[wi] - dt
		if _weapon_cooldowns[wi] > 0.0:
			continue
		var rng: float = float(w["range"])
		var target: Dictionary = _nearest_enemy(px, py, rng)
		if target.is_empty():
			continue
		_weapon_cooldowns[wi] = 1.0 / float(w["fireRate"])
		var base_ang: float = atan2(float(target["dy"]), float(target["dx"]))
		var projectiles: int = int(w["projectiles"])
		var spread: float = 0.18
		for k in range(projectiles):
			if _enemies.size() + _projectiles.size() >= ENTITY_CAP:
				break
			var offset: float = 0.0
			if projectiles > 1:
				offset = (k - (projectiles - 1) / 2.0) * spread
			var ang: float = base_ang + offset
			_projectiles.append({
				"x": px, "y": py,
				"vx": cos(ang) * PROJECTILE_SPEED,
				"vy": sin(ang) * PROJECTILE_SPEED,
				"damage": float(w["damage"]),
				"pierce": int(w["pierce"]),
				"range_left": rng,
			})


func _nearest_enemy(x: float, y: float, max_range: float) -> Dictionary:
	var best: int = -1
	var best_d2: float = max_range * max_range
	var bx: float = 0.0
	var by: float = 0.0
	for i in range(_enemies.size()):
		var en: Dictionary = _enemies[i]
		var d2: float = _dist2(x, y, en["x"], en["y"])
		if d2 <= best_d2:
			best_d2 = d2
			best = i
			bx = en["x"]
			by = en["y"]
	if best < 0:
		return {}
	return {"idx": best, "dx": bx - x, "dy": by - y}


func _move_projectiles(dt: float) -> void:
	var keep: Array = []
	for p: Variant in _projectiles:
		var pr: Dictionary = p
		pr["x"] = float(pr["x"]) + float(pr["vx"]) * dt
		pr["y"] = float(pr["y"]) + float(pr["vy"]) * dt
		pr["range_left"] = float(pr["range_left"]) - PROJECTILE_SPEED * dt
		var ox: float = pr["x"]
		var oy: float = pr["y"]
		if float(pr["range_left"]) <= 0.0 or ox < -20.0 or ox > WORLD_W + 20.0 or oy < -20.0 or oy > WORLD_H + 20.0:
			continue  # drop
		keep.append(pr)
	_projectiles = keep


func _resolve_hits() -> void:
	var hit_r2: float = (PROJECTILE_RADIUS + ENEMY_RADIUS) * (PROJECTILE_RADIUS + ENEMY_RADIUS)
	var live_proj: Array = []
	for p: Variant in _projectiles:
		var pr: Dictionary = p
		var consumed: bool = false
		for e: Variant in _enemies:
			var en: Dictionary = e
			if float(en["hp"]) <= 0.0:
				continue
			if _dist2(pr["x"], pr["y"], en["x"], en["y"]) <= hit_r2:
				en["hp"] = float(en["hp"]) - float(pr["damage"])
				if float(en["hp"]) <= 0.0:
					_kills += 1
				if int(pr["pierce"]) > 0:
					pr["pierce"] = int(pr["pierce"]) - 1
				else:
					consumed = true
					break
		if not consumed:
			live_proj.append(pr)
	_projectiles = live_proj
	# cull dead enemies.
	var live_enemies: Array = []
	for e: Variant in _enemies:
		if float((e as Dictionary)["hp"]) > 0.0:
			live_enemies.append(e)
	_enemies = live_enemies


func _resolve_contact() -> void:
	var hit_r2: float = (PLAYER_RADIUS + ENEMY_RADIUS) * (PLAYER_RADIUS + ENEMY_RADIUS)
	var px: float = _player["x"]
	var py: float = _player["y"]
	for e: Variant in _enemies:
		var en: Dictionary = e
		if float(en["cooldown"]) > 0.0:
			continue
		if _dist2(px, py, en["x"], en["y"]) <= hit_r2:
			_player["hp"] = float(_player["hp"]) - float(en["damage"])
			en["cooldown"] = CONTACT_DAMAGE_COOLDOWN
	if float(_player["hp"]) <= 0.0:
		_player["hp"] = 0.0


static func _dist2(ax: float, ay: float, bx: float, by: float) -> float:
	var dx: float = ax - bx
	var dy: float = ay - by
	return dx * dx + dy * dy


# ── rendering (windowed mode) ────────────────────────────────────────────────
func _build_scene() -> void:
	if _headless:
		return  # no window: skip all node/UI construction
	var bg: ColorRect = ColorRect.new()
	bg.color = Color(0.04, 0.05, 0.09)
	bg.anchor_right = 1.0
	bg.anchor_bottom = 1.0
	add_child(bg)

	_hud = Label.new()
	_hud.position = Vector2(12, 8)
	_hud.add_theme_font_size_override("font_size", 14)
	add_child(_hud)

	if not _data_ok:
		var err_label: Label = Label.new()
		err_label.text = "DATA LOAD FAILED — see stdout"
		err_label.position = Vector2(12, 40)
		err_label.add_theme_color_override("font_color", Color(1, 0.4, 0.4))
		add_child(err_label)
	_update_hud()


func _update_hud() -> void:
	if _hud == null:
		return
	var title: String = str(_meta.get("title", "Aether Scourge"))
	_hud.text = "%s | t=%4.1fs  hp=%d/%d  kills=%d  enemies=%d  proj=%d" % [
		title, _sim_time, int(_player["hp"]), int(_player["max_hp"]),
		_kills, _enemies.size(), _projectiles.size(),
	]


func _draw() -> void:
	if _headless or not _data_ok:
		return
	# player
	draw_circle(Vector2(_player["x"], _player["y"]), PLAYER_RADIUS, Color(0.4, 0.9, 1.0))
	# enemies — tint by behavior so the data shows through visually.
	for e: Variant in _enemies:
		var en: Dictionary = e
		var col: Color = Color(0.9, 0.4, 0.4)
		match str(en["behavior"]):
			"swarm": col = Color(0.95, 0.7, 0.3)
			"ranged": col = Color(0.7, 0.4, 0.9)
			"charger": col = Color(1.0, 0.3, 0.3)
		draw_circle(Vector2(en["x"], en["y"]), ENEMY_RADIUS, col)
	# projectiles
	for p: Variant in _projectiles:
		var pr: Dictionary = p
		draw_circle(Vector2(pr["x"], pr["y"]), PROJECTILE_RADIUS, Color(1.0, 1.0, 0.6))
