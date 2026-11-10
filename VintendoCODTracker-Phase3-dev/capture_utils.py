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
    boxes = [v["box"] for v in items]
    return "\n".join(lines), lines, boxes

def looks_like_scoreboard(lines: list[str]) -> bool:
    """Conservative gate: require several stat headings and four numeric rows."""
    text = " ".join(lines).casefold()
    headings = ("kill", "damage", "assist", "score", "elimination", "revive", "placement")
    heading_hits = sum(1 for word in headings if word in text)
    numeric_rows = sum(1 for line in lines if re.search(r"\d", line))
    return heading_hits >= 2 and numeric_rows >= 4 and len(lines) >= 5
