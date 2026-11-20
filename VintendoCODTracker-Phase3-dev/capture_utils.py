import re

def get_outputs() -> list[dict]:
    """Return display/output identifiers and dimensions reported by DXcam."""
    info = dxcam.output_info()
    outputs = []
    for line in str(info).splitlines():
        match = re.search(
            r"Device\[(\d+)\]\s+Output\[(\d+)\]:\s+Res:\((\d+),\s*(\d+)\).*?Primary:(True|False)",
            line,
        )
        if match:
            device, output, width, height, primary = match.groups()
            outputs.append({
                "device": int(device), "output": int(output),
                "width": int(width), "height": int(height),
                "primary": primary == "True",
                "label": f"Display {output} · {width}×{height}" + (" · primary" if primary == "True" else ""),
            })
    # Attach DXGI's physical monitor handle and desktop bounds. These let the
    # screenshot hotkey map a game window to one exact output, independently
    # of Windows scaling and monitor resolution.
    factory=getattr(dxcam,'__factory',None)
    if factory is not None:
        for item in outputs:
            try:
                native=factory.outputs[item['device']][item['output']]
                rect=native.desc.DesktopCoordinates
                item.update(monitor_handle=int(native.hmonitor),device_name=str(native.devicename),
                            bounds=(int(rect.left),int(rect.top),int(rect.right),int(rect.bottom)))
            except (AttributeError,IndexError,TypeError,ValueError):
                continue
    return outputs

def group_ocr_lines(result) -> tuple[str, list[str], list[tuple]]:
    """Turn RapidOCR boxes into readable lines, preserving box coordinates."""
    text,lines,tokens=group_ocr_lines_detailed(result)
    return text,lines,[box for _,box in tokens]

def group_ocr_lines_detailed(result) -> tuple[str, list[str], list[tuple]]:
    """Return OCR text, grouped lines, and each recognized word with its bounds."""
    if not result:
        return "", [], []
    items = []
    heights = []
    for row in result:
        try:
            box, text, confidence = row
            text = str(text).strip()
            if not text:
                continue
            ys = [float(point[1]) for point in box]
            xs = [float(point[0]) for point in box]
            top, bottom = min(ys), max(ys)
            left, right = min(xs), max(xs)
            heights.append(max(1.0, bottom - top))
            items.append({"text": text, "score": float(confidence), "x": left,
                          "y": (top + bottom) / 2, "box": (left, top, right, bottom)})
        except (TypeError, ValueError, IndexError):
            continue
    if not items:
        return "", [], []
    tolerance = max(9.0, (sum(heights) / max(1, len(heights))) * 0.72)
    groups: list[list[dict]] = []
    for item in sorted(items, key=lambda v: (v["y"], v["x"])):
        target = next((g for g in reversed(groups) if abs(g[0]["y"] - item["y"]) <= tolerance), None)
        if target is None:
            groups.append([item])
        else:
            target.append(item)
            target[0]["y"] = sum(v["y"] for v in target) / len(target)
    groups.sort(key=lambda g: g[0]["y"])
    lines = ["  ".join(v["text"] for v in sorted(g, key=lambda v: v["x"])) for g in groups]
    tokens=[(v["text"],v["box"]) for v in items]
    return "\n".join(lines), lines, tokens

def looks_like_scoreboard(lines: list[str]) -> bool:
    """Only arm auto-capture when OCR sees a player column and several stats."""
    text = " ".join(lines).casefold()
    headings = ("kill", "damage", "assist", "score", "elimination", "revive", "placement", "death")
    heading_hits = sum(1 for word in headings if re.search(r"\b"+word+r"s?\b", text))
    has_player_column = any(re.search(r"\b"+word+r"\b", text) for word in ("player", "players", "username", "gamertag", "operator"))
    numeric_rows = sum(1 for line in lines if re.search(r"\d", line))
    return has_player_column and heading_hits >= 2 and numeric_rows >= 4 and len(lines) >= 5

def _gate_text(lines) -> str:
    try:return " ".join(str(line) for line in (lines or []))
    except Exception:return str(lines or "")

def looks_like_after_action(lines: list[str]) -> bool:
    """After-action report words plus a result and at least one stat heading."""
    text = _gate_text(lines).casefold()
    if not re.search(r"\bafter\s+action\b", text):return False
    has_result = bool(re.search(r"\b(victory|victorious|you win|winner|defeat|defeated|you lose|you lost)\b", text))
    has_stat = any(re.search(r"\b"+word+r"\b", text) for word in ("kill", "kills", "damage", "assist", "assists", "score", "elimination", "eliminations", "death", "deaths", "objective", "captures", "downs"))
    return has_result and has_stat

def looks_like_combat_record(lines: list[str]) -> bool:
    """Combat Record words; lifetime validity is decided by the parser reason."""
    text = _gate_text(lines).casefold()
    return bool(re.search(r"\bcombat\s+record\b", text))

def looks_like_warzone_summary(lines: list[str]) -> bool:
    """Warzone summary/placement words plus squad or placement language."""
    text = _gate_text(lines).casefold()
    if "warzone summary" in text:return True
    if "warzone" not in text:return False
    return any(word in text for word in ("summary", "placement", "squad"))

def looks_like_camo_challenge(lines: list[str]) -> bool:
    """Challenge/camo words plus a weapon class label."""
    text = _gate_text(lines).casefold()
    if not (re.search(r"\bchallenge\b", text) or re.search(r"\bcamo\b", text)):return False
    return any(re.search(r"\b"+re.escape(name)+r"\b", text) for name in ("assault rifle", "submachine gun", "smg", "sniper", "marksman rifle", "light machine gun", "lmg", "shotgun", "handgun", "pistol", "launcher", "melee", "battle rifle", "burst rifle"))

def looks_like_banner_hud(lines: list[str]) -> bool:
    """HUD banner words or a squad-count pattern; PII-free hint only."""
    text = _gate_text(lines).casefold()
    return bool("banner" in text or "hud read" in text or re.search(r"\b\d{1,3}\s+squads?\b", text))
