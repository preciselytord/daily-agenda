#!/usr/bin/env python3
"""Render a daily-agenda markdown file as one designed PDF page (reMarkable 3:4).

Input markdown shape (as produced by the routine):

    # Fri 11 Sep 2026
    ## Meetings
    - 09:00-09:30 Standup (Meet)
    - All day: Offsite
    ## Email
    - [sender] subject - what is needed
    ## Slack
    ## Pull requests
    ## Issues

Usage: agenda_pdf.py AGENDA.md OUT.pdf
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

from reportlab.lib.colors import Color, black, white
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

# reMarkable 2 screen is 1404 x 1872 px (3:4). Use points at the same ratio.
PAGE_W, PAGE_H = 445.5, 594.0
MARGIN = 22.0
GUTTER = 14.0
TIMELINE_W = 218.0
DAY_START, DAY_END = 7, 19          # hours shown on the timeline
FONT, BOLD = "Helvetica", "Helvetica-Bold"

INK = black
GREY = Color(0.45, 0.45, 0.45)
LIGHT = Color(0.72, 0.72, 0.72)
FAINT = Color(0.86, 0.86, 0.86)
FILL = Color(0.93, 0.93, 0.93)

SECTION_ORDER = ["Email", "Slack", "Pull requests", "Issues"]
SECTION_TITLES = {"Email": "EMAIL", "Slack": "SLACK", "Pull requests": "PULL REQUESTS", "Issues": "ISSUES"}


# ---------------------------------------------------------------- parsing

def parse(md: str) -> dict:
    title = ""
    sections: dict[str, list[str]] = {}
    current = None
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = re.match(r"^#\s+(.*)", line)
        if m and not title:
            title = m.group(1).strip()
            continue
        m = re.match(r"^##\s+(.*)", line)
        if m:
            current = m.group(1).strip()
            sections.setdefault(current, [])
            continue
        m = re.match(r"^\s*[-*]\s+(.*)", line)
        if m and current:
            sections[current].append(m.group(1).strip())
        elif current and sections[current]:
            sections[current][-1] += " " + line.strip()
    return {"title": title, "sections": sections}


def parse_date(title: str) -> dt.date | None:
    for fmt in ("%a %d %b %Y", "%A %d %b %Y", "%A %d %B %Y", "%a %d %B %Y", "%Y-%m-%d", "%d %b %Y"):
        try:
            return dt.datetime.strptime(title, fmt).date()
        except ValueError:
            continue
    return None


MEETING_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})\s+(.*)$")


def parse_meetings(items: list[str]) -> tuple[list[dict], list[str]]:
    timed, allday = [], []
    for it in items:
        m = MEETING_RE.match(it)
        if m:
            h1, m1, h2, m2, rest = m.groups()
            timed.append({"start": int(h1) + int(m1) / 60, "end": int(h2) + int(m2) / 60,
                          "label": f"{int(h1):02d}:{m1}-{int(h2):02d}:{m2}", "text": rest.strip()})
        elif re.match(r"(?i)^(all[- ]day:?\s*)", it):
            allday.append(re.sub(r"(?i)^(all[- ]day:?\s*)", "", it).strip())
        elif it.lower().startswith(("calendar connector", "nothing pending", "no meetings")):
            continue
        else:
            allday.append(it)
    timed.sort(key=lambda x: x["start"])
    return timed, allday


# ---------------------------------------------------------------- drawing helpers

def wrap(text: str, font: str, size: float, width: float) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if stringWidth(cand, font, size) <= width or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def ellipsize(text: str, font: str, size: float, width: float) -> str:
    if stringWidth(text, font, size) <= width:
        return text
    while text and stringWidth(text + "…", font, size) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def checkbox(c: canvas.Canvas, x: float, y: float, s: float = 7.0) -> None:
    c.setStrokeColor(INK)
    c.setLineWidth(0.7)
    c.rect(x, y, s, s, stroke=1, fill=0)


def section_heading(c: canvas.Canvas, x: float, y: float, w: float, text: str) -> float:
    c.setFont(BOLD, 8.5)
    c.setFillColor(INK)
    c.drawString(x, y, text)
    c.setStrokeColor(INK)
    c.setLineWidth(0.8)
    c.line(x, y - 4, x + w, y - 4)
    return y - 14


def dot_grid(c: canvas.Canvas, x0: float, y0: float, x1: float, y1: float, step: float = 12.0) -> None:
    c.setFillColor(LIGHT)
    y = y1
    while y >= y0:
        x = x0
        while x <= x1:
            c.circle(x, y, 0.5, stroke=0, fill=1)
            x += step
        y -= step


# ---------------------------------------------------------------- page

def render(md: str, out: Path) -> None:
    data = parse(md)
    date = parse_date(data["title"]) or dt.date.today()
    sections = data["sections"]
    timed, allday = parse_meetings(sections.get("Meetings", []))

    c = canvas.Canvas(str(out), pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"Daily agenda {date.isoformat()}")

    # ---- header
    top = PAGE_H - MARGIN
    c.setFillColor(INK)
    c.setFont(BOLD, 26)
    c.drawString(MARGIN, top - 22, date.strftime("%A").upper())
    c.setFont(FONT, 11)
    c.setFillColor(GREY)
    c.drawString(MARGIN, top - 36, date.strftime("%-d %B %Y"))
    c.setFont(FONT, 8.5)
    right = PAGE_W - MARGIN
    c.drawRightString(right, top - 10, "DAILY AGENDA")
    c.drawRightString(right, top - 22, f"Week {date.isocalendar()[1]}  ·  Day {date.timetuple().tm_yday}")
    # mini week strip: Mon..Sun with today filled
    monday = date - dt.timedelta(days=date.weekday())
    sx = right - 7 * 13
    for i in range(7):
        d = monday + dt.timedelta(days=i)
        cx = sx + i * 13 + 6
        cy = top - 36
        if d == date:
            c.setFillColor(INK)
            c.circle(cx, cy, 5.2, stroke=0, fill=1)
            c.setFillColor(white)
        else:
            c.setFillColor(GREY)
        c.setFont(BOLD if d == date else FONT, 6.5)
        c.drawCentredString(cx, cy - 2.3, "MTWTFSS"[i])
    c.setStrokeColor(INK)
    c.setLineWidth(1.2)
    c.line(MARGIN, top - 46, right, top - 46)

    # ---- layout columns
    col_top = top - 60
    body_bottom = MARGIN + 6
    tl_x = MARGIN
    tl_w = TIMELINE_W
    side_x = tl_x + tl_w + GUTTER
    side_w = right - side_x

    # ---- timeline
    y = col_top
    c.setFont(BOLD, 8.5)
    c.setFillColor(INK)
    c.drawString(tl_x, y, "SCHEDULE")
    y -= 4
    c.setLineWidth(0.8)
    c.line(tl_x, y, tl_x + tl_w, y)
    y -= 6
    if allday:
        c.setFont(FONT, 7.5)
        for a in allday[:3]:
            y -= 9
            c.setFillColor(FILL)
            c.roundRect(tl_x, y - 2.5, tl_w, 10, 2, stroke=0, fill=1)
            c.setFillColor(INK)
            c.drawString(tl_x + 4, y, ellipsize("All day  ·  " + a, FONT, 7.5, tl_w - 8))
        y -= 6
    grid_top = y - 4
    grid_bottom = body_bottom + 150            # leave room for notes below
    hours = DAY_END - DAY_START
    hour_h = (grid_top - grid_bottom) / hours
    label_w = 24
    line_x0 = tl_x + label_w
    line_x1 = tl_x + tl_w
    for i in range(hours + 1):
        hy = grid_top - i * hour_h
        c.setStrokeColor(LIGHT)
        c.setLineWidth(0.5)
        c.line(line_x0, hy, line_x1, hy)
        if i < hours:
            c.setStrokeColor(FAINT)
            c.setLineWidth(0.4)
            c.line(line_x0, hy - hour_h / 2, line_x1, hy - hour_h / 2)
        c.setFillColor(GREY)
        c.setFont(FONT, 6.5)
        c.drawRightString(line_x0 - 4, hy - 2.2, f"{DAY_START + i:02d}")

    def ypos(hour: float) -> float:
        hour = min(max(hour, DAY_START), DAY_END)
        return grid_top - (hour - DAY_START) * hour_h

    # place meetings; handle overlaps by splitting into lanes
    lanes: list[list[dict]] = []
    for m in timed:
        for lane in lanes:
            if all(m["start"] >= o["end"] or m["end"] <= o["start"] for o in lane):
                lane.append(m)
                m["lane"] = lanes.index(lane)
                break
        else:
            lanes.append([m])
            m["lane"] = len(lanes) - 1
    nlanes = max(1, len(lanes))
    lane_w = (line_x1 - line_x0 - 2) / nlanes
    for m in timed:
        y1 = ypos(m["start"])
        y0 = ypos(m["end"])
        if y1 - y0 < 9:
            y0 = y1 - 9
        bx = line_x0 + 1 + m["lane"] * lane_w
        bw = lane_w - 2
        c.setFillColor(FILL)
        c.setStrokeColor(INK)
        c.setLineWidth(0.7)
        c.roundRect(bx, y0, bw, y1 - y0, 2.5, stroke=1, fill=1)
        c.setFillColor(INK)
        c.rect(bx, y0, 2.2, y1 - y0, stroke=0, fill=1)
        c.setFont(BOLD, 6.8)
        c.drawString(bx + 5, y1 - 8, ellipsize(m["label"] + "  " + m["text"], BOLD, 6.8, bw - 8)
                     if y1 - y0 < 17 else m["label"])
        if y1 - y0 >= 17:
            c.setFont(FONT, 7)
            lines = wrap(m["text"], FONT, 7, bw - 8)
            max_lines = max(1, int((y1 - y0 - 12) / 8.5))
            for i, ln in enumerate(lines[:max_lines]):
                if i == max_lines - 1 and len(lines) > max_lines:
                    ln = ellipsize(ln + " " + " ".join(lines[max_lines:]), FONT, 7, bw - 8)
                c.drawString(bx + 5, y1 - 17 - i * 8.5, ln)

    # ---- notes area under the timeline
    ny = grid_bottom - 16
    c.setFont(BOLD, 8.5)
    c.setFillColor(INK)
    c.drawString(tl_x, ny, "NOTES")
    c.setLineWidth(0.8)
    c.setStrokeColor(INK)
    c.line(tl_x, ny - 4, tl_x + tl_w, ny - 4)
    dot_grid(c, tl_x + 2, body_bottom, tl_x + tl_w, ny - 14)

    # ---- right column sections
    y = col_top
    item_font_size = 7.2
    for key in SECTION_ORDER:
        items = sections.get(key, [])
        y = section_heading(c, side_x, y, side_w, SECTION_TITLES[key])
        if not items:
            items = ["nothing pending"]
        for it in items:
            placeholder = it.lower().startswith(("nothing pending", "connector not connected", "github not accessible")) \
                or "connector not connected" in it.lower()
            lines = wrap(it, FONT, item_font_size, side_w - 13)
            needed = 9.2 * len(lines) + 2
            if y - needed < body_bottom + 4:
                c.setFillColor(GREY)
                c.setFont(FONT, 6.5)
                c.drawString(side_x + 13, y - 6, "…")
                y -= 10
                break
            if placeholder:
                c.setFillColor(GREY)
                c.setFont(FONT, item_font_size)
                c.drawString(side_x + 13, y - 6, lines[0])
                y -= needed + 1
                continue
            checkbox(c, side_x + 1, y - 7.2)
            c.setFillColor(INK)
            c.setFont(FONT, item_font_size)
            for i, ln in enumerate(lines):
                c.drawString(side_x + 13, y - 6 - i * 9.2, ln)
            y -= needed + 1
        y -= 9
        if y < body_bottom + 30:
            break

    # ---- footer
    c.setFillColor(LIGHT)
    c.setFont(FONT, 6)
    c.drawRightString(right, MARGIN - 8, f"generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    c.showPage()
    c.save()


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    render(Path(sys.argv[1]).read_text(), Path(sys.argv[2]))


if __name__ == "__main__":
    main()
