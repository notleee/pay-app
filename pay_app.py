#!/usr/bin/env python3
"""
Оплати: програма з вікном і кнопками.
Читає канал від імені вашого акаунта Telegram, рахує заробіток обраної групи,
має темну/світлу тему, довільний колір акценту і сповіщення про нові оплати за типами.
Групи редагуються у вікні (кнопка ✎), є підсумок за період, план з прогресом,
ваш відсоток і конвертація в гривні.

Установка:  pip install telethon tzdata
Запуск:     двічі клацніть по pay_app.py  (або зберіть .exe через build_app.bat)
"""
import asyncio
import io
import json
import math
import os
import queue
import random
import re
import struct
import subprocess
import sys
import threading
import time
import traceback
import wave
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, simpledialog, colorchooser
    from tkinter import font as tkfont
except ImportError:
    tk = None
try:
    from telethon import TelegramClient, events
except ImportError:
    TelegramClient = events = None
OCR_ERR = ""
try:
    import pytesseract
    from PIL import Image
except Exception as _e:
    pytesseract = Image = None
    OCR_ERR = f"{type(_e).__name__}: {_e}"
OCR_OK = pytesseract is not None

try:
    TZ = ZoneInfo("Europe/Kyiv")
except Exception:
    TZ = ZoneInfo("Europe/Kiev")

BASE = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE, "pay_config.json")

import logging
try:
    logging.basicConfig(filename=os.path.join(BASE, "pay_app.log"), level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", encoding="utf-8")
except Exception:
    pass


def log_note(msg):
    """Короткий запис про сповіщення у pay_notify.log (для діагностики)."""
    try:
        with open(os.path.join(BASE, "pay_notify.log"), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%H:%M:%S}] {msg}\n")
    except Exception:
        pass


def log_error(where):
    """Записати помилку у pay_app_error.log (раніше такі помилки зникали мовчки)."""
    try:
        with open(os.path.join(BASE, "pay_app_error.log"), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {where}\n{traceback.format_exc()}\n")
    except Exception:
        pass

# ---------- ГРУПИ ----------
DEFAULT_GROUPS = {
    "1": ["luca star", "marius", "anicka", "teapartyanicka", "alexa"],
    "2": ["master on twinks", "mot", "marie sauvage", "benny", "ben ryder"],
    "3": ["lola", "wolver peen", "alessia", "lula stocch"],
    "4": ["abelia", "noah", "luiz silva", "alex gonzalez"],
}
SHORT = {"master on twinks": "MoT"}
GROUPS = {}   # заповнюється з pay_config.json або DEFAULT_GROUPS (див. apply_groups)
PAIRS = []
PLAN_MODES = {"day": "День", "month": "Місяць"}
WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
# Ручні значення (чисті, $) за дні, яких немає в каналі: 1–3 листопада і частина 4-го.
# Додаються до плану «Місяць», якщо місяць повністю покриває ці дні.
MANUAL = {"from": date(2026, 11, 1), "to": date(2026, 11, 4), "items": [
    ("Benny", 1313), ("Benny BigWish", 626),
    ("Master On Twinks", 1904), ("Master On Twinks BigWish", 125),
    ("Marie Sauvage", 1554), ("Marie Sauvage BigWish", 1922),
    ("Ben Ryder", 379), ("Ben Ryder BigWish", 0),
]}
# ---------- ТИПИ ОПЛАТ (для сповіщень) ----------
TYPES = [("ppv", "PPV", "#2f80ed"), ("tip", "Чайові", "#f2994a"),
         ("new_sub", "Нова підписка", "#27ae60"), ("renew", "Продовження підписки", "#9b51e0"),
         ("other", "Інше", "#8a97a0")]
TYPE_LABEL = {k: l for k, l, _ in TYPES}
TYPE_COLOR = {k: c for k, _, c in TYPES}
SWATCHES = ["#0b6b5d", "#2f80ed", "#9b51e0", "#e5484d", "#f2994a", "#d6409f", "#3b3f46"]
BG_SWATCHES = ["#0a1230", "#0d1215", "#111214", "#0a0714", "#08141c", "#eef2f1", "#ffffff"]
SURF_SWATCHES = ["#121d46", "#151c20", "#1a1c20", "#140e26", "#0e202c", "#ffffff", "#e9f4ef"]


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", s.lower())).strip()


def apply_groups(groups):
    """Застосувати склад груп і перебудувати таблицю пошуку анкет."""
    clean = {}
    if isinstance(groups, dict):
        for g, al in groups.items():
            seen = []
            for a in al if isinstance(al, list) else []:
                a = norm(str(a))
                if a and a not in seen:
                    seen.append(a)
            clean[str(g)] = seen
    if not clean:
        clean = {g: list(al) for g, al in DEFAULT_GROUPS.items()}
    GROUPS.clear()
    GROUPS.update(clean)
    PAIRS[:] = sorted(((a, g) for g, al in GROUPS.items() for a in al), key=lambda p: -len(p[0]))


def group_keys(d=None):
    d = GROUPS if d is None else d
    return sorted(d, key=lambda g: (not g.isdigit(), int(g) if g.isdigit() else 0, g))


apply_groups(None)


def find_group(model):
    n = norm(model)
    for alias, g in PAIRS:
        if alias == "mot":
            if n == "mot":
                return g
            continue
        if alias in n:
            return g
    return "—"


# ---------- ПАРСИНГ І ПІДРАХУНОК ----------
RE_PAY = re.compile(r"💸\s*\$([\d,]+(?:\.\d+)?)\s*·\s*([^\n]+)")
RE_MODEL = re.compile(r"Модель:\s*([^\n]+)")
# Чисті = сума оплати мінус комісія: чайові BigWish і оплати з Para −17%, усі інші оплати −20%
CUT_BIGWISH_TIP = 0.17
CUT_OTHER = 0.20


SHIFT_DEFS = [("night", "Нічна", "00–08", 0, 8), ("morning", "Ранкова", "08–16", 8, 16),
              ("evening", "Вечірня", "16–24", 16, 24)]
ALL_SHIFTS = {k for k, *_ in SHIFT_DEFS}
RE_TIME = re.compile(r"🕒\s*\d{1,2}\.\d{1,2}\s+(\d{1,2}):(\d{2})")


def shift_of(dt):
    for key, _n, _s, a, b in SHIFT_DEFS:
        if a <= dt.hour < b:
            return key
    return "evening"


def pay_dt(msg_dt, hm):
    """Реальний час оплати: година з рядка 🕒, дата з повідомлення (з поправкою на перехід через північ)."""
    if not hm or hm[0] > 23 or hm[1] > 59:
        return msg_dt
    cand = datetime.combine(msg_dt.date(), dtime(hm[0], hm[1]), TZ)
    if cand > msg_dt + timedelta(minutes=5):
        cand -= timedelta(days=1)
    return cand


def num(s):
    return float(s.replace(",", ""))


def fmt(x):
    return f"${x:,.2f}"


def classify(kind):
    k = kind.lower()
    if "підписк" in k:
        return "renew" if "продовж" in k else "new_sub"
    if "чайов" in k:
        return "tip"
    if "ppv" in k:
        return "ppv"
    return "other"


def parse(text):
    out = []
    for block in re.split(r"(?=💸)", text or ""):
        m, mm = RE_PAY.search(block), RE_MODEL.search(block)
        if not m or not mm:
            continue
        gross = num(m.group(1))
        kind = m.group(2).strip()
        bigwish = "bigwish" in re.sub(r"\s+", "", block.lower())
        para = bool(re.search(r"\bpara\b", block, re.I))
        cut = CUT_BIGWISH_TIP if (bigwish and classify(kind) == "tip") or para else CUT_OTHER
        model = re.sub(r"\s*\(OnlyFans\)\s*$", "", mm.group(1)).strip()
        t = RE_TIME.search(block)
        out.append({"gross": gross, "net": gross * (1 - cut), "bigwish": bigwish, "para": para, "nocut": bigwish or para,
                    "kind": kind, "model": model,
                    "hm": (int(t.group(1)), int(t.group(2))) if t else None})
    return out


def aggregate(pays, group, exclude_subs=True, shifts=None):
    shifts = shifts or ALL_SHIFTS
    data, subs, subs_sum, other = {}, 0, 0.0, 0
    for p in pays:
        if p.get("dt") is not None and shift_of(p["dt"]) not in shifts:
            continue
        g = find_group(p["model"])
        if group != "all" and g != group:
            other += 1
            continue
        if exclude_subs and "підписк" in p["kind"].lower():
            subs += 1
            subs_sum += p["net"]
            continue
        d = data.setdefault(g, {}).setdefault(p["model"], [0, 0.0, 0.0, 0.0])
        d[0] += 1
        d[1] += p["net"]
        if p.get("bigwish"):
            d[2] += p["net"]
        elif p.get("para"):
            d[3] += p["net"]
    vals = [v for m in data.values() for v in m.values()]
    return {"data": data, "tc": sum(v[0] for v in vals), "tn": sum(v[1] for v in vals),
            "subs": subs, "subs_sum": subs_sum, "other": other}


def bigwish_full(pays):
    """BigWish і Para рахуємо без комісії (повна сума), решта оплат — як є (чисті)."""
    return [dict(p, net=p["gross"]) if (p.get("bigwish") or p.get("para")) else p for p in pays]


def display_name(model):
    return SHORT.get(norm(model), model)


def shift_title(shifts):
    shifts = shifts or ALL_SHIFTS
    if len(shifts) >= len(ALL_SHIFTS):
        return "усі зміни"
    return " + ".join(n.lower() for k, n, *_ in SHIFT_DEFS if k in shifts)


def report_text(r, group, day, exclude_subs=True, shifts=None):
    title = "усі групи" if group == "all" else f"група {group}"
    shifts = shifts or ALL_SHIFTS
    if len(shifts) < len(ALL_SHIFTS):
        title += ", " + shift_title(shifts)
    lines = [f"{day}, {title}" + (" (без підписок)" if exclude_subs else "")]
    for g in sorted(r["data"]):
        if group == "all":
            lines.append("Без групи:" if g == "—" else f"Група {g}:")
        for model, (c, ne, bw, para) in sorted(r["data"][g].items(), key=lambda kv: -kv[1][1]):
            extra = (f" · BW {fmt(bw)}" if bw else "") + (f" · Para {fmt(para)}" if para else "")
            lines.append(f"{display_name(model)}: {fmt(ne)} ({c} пл.){extra}")
    lines.append(f"РАЗОМ: {r['tc']} пл. · чистими {fmt(r['tn'])}")
    if r["subs"]:
        lines.append(f"Виключено підписок: {r['subs']} ({fmt(r['subs_sum'])})")
    return "\n".join(lines)


def parse_date(s):
    s = str(s or "").strip()
    for f in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def parse_dt(s, today=None):
    """«14.11 08:00», «14.11.2026 8», «2026-11-14 08:30» -> datetime (Київ) або None."""
    today = today or datetime.now(TZ).date()
    m = re.fullmatch(r"\s*(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)\.?\s+(\d{1,2})(?::(\d{2}))?\s*", str(s or ""))
    if not m:
        return None
    ds, hh, mm = m.group(1), int(m.group(2)), int(m.group(3) or 0)
    d = parse_date(ds)
    if d is None:
        a = re.fullmatch(r"(\d{1,2})\.(\d{1,2})", ds)
        if not a:
            return None
        try:
            d = date(today.year, int(a.group(2)), int(a.group(1)))
        except ValueError:
            return None
    if hh > 23 or mm > 59:
        return None
    return datetime.combine(d, dtime(hh, mm), TZ)


def manual_total(group, d1, d2):
    """Ручні значення за 1–4.11 (чисті), якщо період повністю їх покриває; інакше None."""
    if not (d1 <= MANUAL["from"] and d2 >= MANUAL["to"]):
        return None
    return float(sum(v for m, v in MANUAL["items"] if group == "all" or find_group(m) == group))


def fmt0(x):
    return f"${x:,.0f}"


RING, RING_R, RING_W = 100, 33, 7   # розмір полотна, радіус і товщина кільця плану


def money_short(x):
    """$100 для цілих сум, $19.91 для решти."""
    return f"${x:,.0f}" if abs(x - round(x)) < 0.005 else f"${x:,.2f}"


def ago(dt):
    """«щойно», «2 хв тому», «3 год тому», «2 дн тому»."""
    if dt is None:
        return ""
    s_ = max((datetime.now(TZ) - dt).total_seconds(), 0)
    if s_ < 60:
        return "щойно"
    m = int(s_ // 60)
    if m < 60:
        return f"{m} хв тому"
    h = m // 60
    if h < 24:
        return f"{h} год тому"
    return f"{h // 24} дн тому"


_PAY_WAV = None


def pay_sound_bytes():
    """Новорічні дзвіночки (три удари із затуханням), створюються в пам'яті: окремих файлів не потрібно."""
    global _PAY_WAV
    if _PAY_WAV is None:
        sr = 44100
        buf = [0.0] * int(sr * 1.3)
        # (співвідношення частоти, гучність, швидкість затухання): негармонійні обертони дають «дзвін»
        partials = ((1.0, 1.0, 4.5), (2.0, 0.45, 6.0), (2.76, 0.35, 8.0), (5.40, 0.15, 13.0))
        for start, freq in ((0.0, 1318.51), (0.14, 1567.98), (0.28, 2093.00)):      # мі, соль, до
            i0 = int(start * sr)
            for i in range(len(buf) - i0):
                t = i / sr
                env = min(1.0, t / 0.002) * min(1.0, (len(buf) - i0 - i) / (0.05 * sr))
                buf[i0 + i] += env * sum(vol * math.exp(-decay * t) * math.sin(2 * math.pi * freq * ratio * t)
                                         for ratio, vol, decay in partials)
        peak = max(abs(x) for x in buf) or 1.0
        pcm = b"".join(struct.pack("<h", int(x / peak * 0.55 * 32767)) for x in buf)
        bio = io.BytesIO()
        with wave.open(bio, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm)
        _PAY_WAV = bio.getvalue()
    return _PAY_WAV


def play_pay_sound(root=None):
    try:
        import winsound
        data = pay_sound_bytes()
        # Python не дозволяє SND_ASYNC разом зі звуком із пам'яті, тому граємо в окремому потоці
        threading.Thread(target=winsound.PlaySound, args=(data, winsound.SND_MEMORY), daemon=True).start()
    except Exception:
        try:
            if root is not None:
                root.bell()
        except Exception:
            pass


CHATTER_FEE = 0.17  # комісія з BigWish і Para: віднімається від суми перед нарахуванням відсотка
# Відсотки чатера по анкетах: звичайні оплати (rev) / Para / BigWish (bw)
CHATTER_PCT = {
    "Benny": {"rev": 8, "para": 10, "bw": 10},
    "MoT": {"rev": 8, "para": 10, "bw": 10},
    "Marie": {"rev": 10, "bw": 12},
    "Ben Ryder": {"rev": 13, "para": 15, "bw": 15},
}

# ---------- ПЛАН З ФОТО (Tesseract OCR) ----------
RE_USD = re.compile(r"\$\s*((?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[.,]\d{1,2})?)")
RE_TOTAL = re.compile(r"tota[l1i|]", re.I)


def usd_values(line):
    out = []
    for m in RE_USD.finditer(line):
        try:
            out.append(float(re.sub(r"[ \u00a0]", "", m.group(1)).replace(",", ".")))
        except ValueError:
            pass
    return out


def parse_plan_text(text):
    """Розпізнаний текст таблиці -> {"shift","day"} (фото «Shift plan/Day plan») або {"month"} (фото «Plan payments»)."""
    low, lines = text.lower(), text.splitlines()
    if "revenue" in low:
        dm = re.search(r"(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{4})", text)
        try:
            when = date(int(dm.group(3)), int(dm.group(2)), int(dm.group(1))).isoformat() if dm else None
        except ValueError:
            when = None

        def month_res(plan, done):
            res = {"month": plan}
            if done and when:
                res.update({"month_done": done, "month_date": when})
            return res
        for ln in lines:
            if RE_TOTAL.search(ln):
                v = usd_values(ln)
                if len(v) >= 4:
                    return month_res(v[-1], v[-3])
        for i, ln in enumerate(lines[:-1]):  # запасний варіант: блок «TOTAL | PLAN»
            if RE_TOTAL.search(ln) and "plan" in ln.lower():
                v = usd_values(lines[i + 1])
                if len(v) >= 2:
                    return month_res(v[1], v[0])
        return None
    if "shift" in low and "day" in low:
        for ln in lines:
            if RE_TOTAL.search(ln):
                v = usd_values(ln)
                if len(v) >= 2 and v[0] > 0 and v[1] > 0:
                    return {"shift": v[0], "day": v[1]}
    return None


def chatter_model(text):
    t = text.lower()
    if "benny" in t:
        return "Benny"
    if "ryder" in t:
        return "Ben Ryder"
    if "marie" in t or "sauvage" in t:
        return "Marie"
    if re.search(r"\bmot\b", t) or "twinks" in t:
        return "MoT"
    return None


def _clean_tok(t):
    return t.strip().strip("|[]()!;:.,_")


def line_amounts(tokens):
    """tokens: [(text, left, right)] одного рядка -> [(сума, x-центр)]; склеює «$22» + «605» в $22 605."""
    out, i = [], 0
    while i < len(tokens):
        t, l, r = tokens[i]
        t = t.strip("|[]()!;_ ")
        if t.startswith("$") and len(t) > 1:
            parts, right, j = [t.rstrip("|[]()!;.,_ ")], r, i + 1
            while j < len(tokens):
                nt = tokens[j][0].strip("|[]()!;_ ")
                if re.fullmatch(r"\d{3}(?:[.,]\d{1,2})?[.,]?", nt) and "," not in parts[-1] and "." not in parts[-1]:
                    parts.append(nt.rstrip(".,"))
                    right = tokens[j][2]
                    j += 1
                else:
                    break
            v = usd_values(" ".join(parts))
            if v:
                out.append((v[0], (l + right) / 2))
            i = j
        else:
            i += 1
    return out


def parse_month_models(d):
    """Результат pytesseract.image_to_data (dict) -> {анкета: {"rev","bw","para"}} з таблиці «REVENUE monthly»."""
    lines, para_left = {}, None
    for k in range(len(d["text"])):
        t = (d["text"][k] or "").strip()
        if not t:
            continue
        l, r = d["left"][k], d["left"][k] + d["width"][k]
        low = _clean_tok(t).lower()
        if low == "para":  # заголовок вирівняно ліворуч, тож межа колонки Para = його лівий край
            para_left = l
        lines.setdefault((d["block_num"][k], d["par_num"][k], d["line_num"][k]), []).append((t, l, r))
    res = {}
    for toks in lines.values():
        toks.sort(key=lambda x: x[1])
        name = chatter_model(" ".join(t for t, _l, _r in toks))
        if not name or name in res:
            continue
        am = line_amounts(toks)
        if len(am) < 4:
            continue
        head = am[:-3]
        total = am[-3][0]
        rev, bw, para = head[0][0], 0.0, 0.0
        if len(head) >= 3:
            bw, para = head[1][0], head[2][0]
        elif len(head) == 2:
            v, x = head[1]
            if para_left is not None and x >= para_left:
                para = v
            else:
                bw = v
        if abs(rev + bw + para - total) <= max(5.0, total * 0.02):
            res[name] = {"rev": rev, "bw": bw, "para": para}
    return res


def ocr_clean(img, thr):
    """Прибирає кольорові клітинки і лінії таблиці, лишає чорний текст на білому."""
    img = img.convert("L")
    w, h = img.size
    img = img.resize((w * 3, h * 3), Image.LANCZOS).point(lambda v: 0 if v < thr else 255)
    w, h = img.size
    rows = list(img.resize((1, h), Image.BOX).tobytes())
    cols = list(img.resize((w, 1), Image.BOX).tobytes())
    px = img.load()
    for y, m in enumerate(rows):
        if m < 150:
            for x in range(w):
                px[x, y] = 255
    for x, m in enumerate(cols):
        if m < 150:
            for y in range(h):
                px[x, y] = 255
    out = Image.new("L", (w + 40, h + 40), 255)
    out.paste(img, (20, 20))
    return out


def default_tesseract():
    for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        if os.path.exists(p):
            return p
    return None


def read_plan_image(data, cmd=None):
    cmd = (cmd or "").strip().strip('"') or default_tesseract()
    if cmd and os.path.isdir(cmd):  # вказали теку замість файлу
        cmd = os.path.join(cmd, "tesseract.exe")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    img = Image.open(io.BytesIO(data))
    best = None
    try:
        for thr in (100, 140, 70):
            clean = ocr_clean(img, thr)
            res = parse_plan_text(pytesseract.image_to_string(clean, config="--psm 6"))
            if not res:
                continue
            if "month" not in res:
                return res
            d = pytesseract.image_to_data(clean, config="--psm 6", output_type=pytesseract.Output.DICT)
            res["models"] = parse_month_models(d)
            if len(res["models"]) >= len(CHATTER_PCT):
                return res
            if best is None or len(res["models"]) > len(best["models"]):
                best = res
        return best
    except PermissionError as e:
        raise RuntimeError(f"Windows не дозволяє запустити Tesseract ({cmd or 'tesseract'}): {e}.\n"
                           "Перевірте шлях до tesseract.exe в «Налаштування» (має бути файл, а не тека) "
                           "і чи не блокує його антивірус.")
    except pytesseract.TesseractNotFoundError:
        raise RuntimeError("Tesseract OCR не знайдено. Встановіть його (github.com/UB-Mannheim/tesseract/wiki) "
                           "або вкажіть шлях до tesseract.exe в «Налаштування».")
    return None


def is_image_msg(m):
    return bool(m.photo) or bool(m.document and (getattr(m.document, "mime_type", "") or "").startswith("image/"))


def load_cfg():
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(cfg):
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------- ТЕМА ----------
LIGHT = {"bg": "#eef2f1", "surf": "#ffffff", "ink": "#14201d", "muted": "#68786f", "line": "#d7e0dc"}
DARK = {"bg": "#0d1215", "surf": "#151c20", "ink": "#e9eff1", "muted": "#8696a0", "line": "#243038"}
P = {}


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    return "#%02x%02x%02x" % tuple(round(x * (1 - t) + y * t) for x, y in zip(hex_rgb(a), hex_rgb(b)))


def style_titlebar(win):
    """Windows 10/11: темний заголовок вікна замість білого (колір береться з поточної теми)."""
    if sys.platform != "win32":
        return

    def apply():
        try:
            import ctypes
            win.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
            dwm = ctypes.windll.dwmapi
            dark = ctypes.c_int(1 if lum(P["bg"]) < 0.5 else 0)
            for attr in (20, 19):  # 20 — Win10 20H1+/Win11, 19 — старіші збірки Win10
                if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(dark), 4) == 0:
                    break
            for attr, col in ((35, P["bg"]), (36, P["ink"]), (34, P["line"])):  # заголовок, текст, рамка (Win11)
                r_, g_, b_ = hex_rgb(col)
                val = ctypes.c_int(r_ | (g_ << 8) | (b_ << 16))
                dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), 4)
        except Exception:
            pass
    apply()
    try:
        win.after(150, apply)
    except Exception:
        pass


def lum(h):
    r, g, b = hex_rgb(h)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


# Готові палітри: основа (фон, картки, текст, лінії) + акцент за замовчуванням. «Класика» = світла/темна тема з ваш акцентом.
PALETTES = {
    # deco: «характер» палітри — що малюється в шапці (xmas / neon / graphite / ocean / mint)
    "Неон": {"theme": "dark", "bg": "#0a0714", "surf": "#140e26", "ink": "#f1ebff", "muted": "#9a8cc4",
             "line": "#2b2050", "accent": "#ff2bd6", "acc2": "#00e5ff", "deco": "neon"},        # рожевий + блакитний, ретро-сонце
    "Графіт": {"theme": "dark", "bg": "#111214", "surf": "#1a1c20", "ink": "#e7e9ec", "muted": "#8a9099",
               "line": "#2b2e34", "accent": "#7aa2f7", "deco": "graphite"},                    # креслення + стовпчики
    "Океан": {"theme": "dark", "bg": "#06121c", "surf": "#0c2231", "ink": "#e4f1f7", "muted": "#7fa3b5",
              "line": "#1a3a50", "accent": "#2bb3d9", "acc2": "#2dd4bf", "deco": "ocean"},     # хвилі та бульбашки
    "М'ята": {"theme": "light", "bg": "#e6f4ec", "surf": "#ffffff", "ink": "#10241d", "muted": "#5f7a6e",
              "line": "#c8e2d4", "accent": "#12a37f", "acc2": "#f2b84b", "deco": "mint"},     # пагорби, сонце, пилок
    "Новорічна": {"theme": "dark", "bg": "#07201a", "surf": "#0e3329", "ink": "#fff6e0", "muted": "#9cc4b0",
                  "line": "#1f5a45", "accent": "#f2c14e", "acc2": "#d62839", "deco": "xmas"},  # ялинкова зелень, золото, червоний
}


def xmas():
    """Новорічне оформлення (шапка Діда Мороза, сніжинки) — лише в палітрі «Новорічна»."""
    return bool(SANTA_HAT) and P.get("deco") == "xmas"


def short_status(text):
    """Скорочений статус для шапки: повний текст показується підказкою при наведенні."""
    if text.startswith("Канал"):
        m = re.search(r"(\d\d:\d\d):\d\d$", text)
        return "онлайн" + (f" · {m.group(1)}" if m else "")
    return text if len(text) <= 26 else text[:25] + "…"


def plan_color(f):
    """Колір смуги плану: червоний -> жовтий -> зелений залежно від виконання."""
    if f >= 1:
        return "#27ae60"
    f = max(f, 0.0)
    if f < 0.5:
        return mix("#e5484d", "#f2b84b", f / 0.5)
    return mix("#f2b84b", "#27ae60", (f - 0.5) / 0.5)


# ---------- ОНОВЛЕННЯ ПРОГРАМИ ----------
APP_VERSION = "1.0.0"     # збільшуйте перед кожним випуском: 1.0.1, 1.1.0 …
# Посилання, з якого програма дізнається про нову версію (це ваш «канал оновлень»). Два варіанти:
#   1) GitHub Releases:  https://api.github.com/repos/ВАШ_ЛОГІН/ВАШ_РЕПО/releases/latest
#      (публічний репозиторій; у релізі — файл pay_app.exe, а назва тега = версія, напр. v1.0.1)
#   2) свій version.json:  {"version": "1.0.1", "url": "https://…/pay_app.exe", "notes": "що нового", "sha256": "…"}
#      (його створює make_release.py; файл можна покласти на будь-який https-хостинг)
UPDATE_URL = "https://api.github.com/repos/notleee/pay-app/releases/latest"


def ver_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4]) or (0,)


def update_url(cfg):
    return str((cfg or {}).get("UPDATE_URL") or UPDATE_URL or "").strip()


def _check_link(url):
    u = url.lower()
    if not (u.startswith("https://") or u.startswith("http://127.0.0.1") or u.startswith("http://localhost")):
        raise ValueError("Посилання на оновлення має починатися з https://")


def parse_update_info(data):
    """version.json або відповідь GitHub Releases API → {version, url, py_url, notes, sha256}."""
    if not isinstance(data, dict):
        raise ValueError("Некоректна відповідь сервера оновлень")
    if "tag_name" in data:      # GitHub Releases
        assets = data.get("assets") or []
        pick = lambda ext: next((a for a in assets if str(a.get("name", "")).lower().endswith(ext)), None) or {}
        exe, py = pick(".exe"), pick(".py")
        dig = str(exe.get("digest") or "")
        return {"version": str(data.get("tag_name") or ""), "url": exe.get("browser_download_url") or "",
                "py_url": py.get("browser_download_url") or "", "notes": str(data.get("body") or ""),
                "sha256": dig.split(":", 1)[1] if dig.startswith("sha256:") else ""}
    return {k: str(data.get(k) or "") for k in ("version", "url", "py_url", "notes", "sha256")}


def fetch_update_info(url, timeout=12):
    import urllib.request
    _check_link(url)
    req = urllib.request.Request(url, headers={"User-Agent": "pay_app-updater", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return parse_update_info(json.loads(r.read().decode("utf-8")))


def download_file(url, dest, progress=None, sha256="", timeout=30):
    """Завантажує файл у dest (через .part). Перевіряє SHA-256, якщо його вказано."""
    import hashlib
    import urllib.request
    _check_link(url)
    h, tmp = hashlib.sha256(), dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "pay_app-updater"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
            total, got = int(r.headers.get("Content-Length") or 0), 0
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total)
        if sha256 and h.hexdigest().lower() != sha256.strip().lower():
            raise ValueError("Контрольна сума не збігається — файл пошкоджено, оновлення скасовано")
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def validate_download(path, is_exe):
    with open(path, "rb") as f:
        raw = f.read()
    if is_exe:
        if raw[:2] != b"MZ" or len(raw) < 1_000_000:
            raise ValueError("Завантажений файл не схожий на програму (.exe)")
    else:
        compile(raw.decode("utf-8"), path, "exec")      # має бути коректний Python-код


def _relaunch_later(args):
    """Запускає програму знову через ~4 с — коли ця копія вже закрилась і відпустила сесію Telegram."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI") and k != "_MEIPASS2"}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if os.name == "nt":
        cmd = "cmd /c ping -n 5 127.0.0.1 >nul & start \"\" " + " ".join(f'"{a}"' for a in args)
        subprocess.Popen(cmd, env=env, cwd=BASE, creationflags=0x08000000 | 0x00000200, close_fds=True)
    else:
        subprocess.Popen(["sh", "-c", "sleep 4; exec \"$@\"", "sh"] + list(args), env=env, cwd=BASE, start_new_session=True)


def apply_update(new_path, frozen=None, target=None):
    """Підміняє програму новим файлом і планує перезапуск. Працює і для .exe, і для pay_app.py.
    Запущений .exe не можна перезаписати, але його можна перейменувати — старий лишається як .old."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    target = target or (sys.executable if frozen else os.path.abspath(__file__))
    backup = target + (".old" if frozen else ".bak")
    if os.path.exists(backup):
        try:
            os.remove(backup)
        except OSError:
            pass
    os.replace(target, backup)
    try:
        os.replace(new_path, target)
    except Exception:
        os.replace(backup, target)      # не вийшло — повертаємо як було
        raise
    return [target] if frozen else [sys.executable, target]


def cleanup_update_leftovers():
    exe = sys.executable if getattr(sys, "frozen", False) else None
    for p in ([exe + ".old", exe + ".new", exe + ".new.part"] if exe else []):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


# ---------- ШАПКА ДІДА МОРОЗА ----------
SANTA_HAT = True   # False — прибрати шапку після свят


def _curve(p0, p1, p2, n=12):
    """Точки квадратичної кривої Безьє."""
    return [tuple((1 - t) ** 2 * a + 2 * (1 - t) * t * b + t * t * c for a, b, c in zip(p0, p1, p2))
            for t in (i / n for i in range(n + 1))]


def santa_hat_shapes():
    """Деталі шапки в координатах значка 100×100 (верх значка — y=0): (тип, колір, точки)."""
    cone = (_curve((14, 6), (28, -30), (60, -43)) + _curve((60, -43), (95, -48), (112, 4))
            + _curve((112, 4), (104, 10), (98, 6)) + _curve((98, 6), (91, -18), (76, -20))
            + _curve((76, -20), (84, -8), (86, 6)))
    # дзеркалимо, щоб кінчик звисав ліворуч і не заважав назві
    cone = [(100 - x, y) for x, y in cone]
    return [("poly", "#d62839", cone),
            ("oval", "#ffffff", (-22, -1, 4, 25)),          # помпон
            ("pill", "#ffffff", (10, -4, 96, 16))]           # хутряна облямівка


def draw_hat(c, bx, by, side):
    """Шапка Діда Мороза на полотні Tk; (bx, by, side) — квадрат, на який вона «одягається»."""
    k = side / 100
    for kind, color, pts in santa_hat_shapes():
        if kind == "poly":
            c.create_polygon([v for x, y in pts for v in (bx + x * k, by + y * k)], fill=color, outline="", tags="fg")
            continue
        x1, y1, x2, y2 = (v * k for v in pts)
        if kind == "oval":
            c.create_oval(bx + x1, by + y1, bx + x2, by + y2, fill=color, outline="#c9d3dc", tags="fg")
        else:   # заокруглена смужка: товста лінія з круглими кінцями
            r = (y2 - y1) / 2
            line = (bx + x1 + r, by + y1 + r, bx + x2 - r, by + y1 + r)
            c.create_line(*line, fill="#c9d3dc", width=2 * r + 2, capstyle="round", tags="fg")
            c.create_line(*line, fill=color, width=2 * r, capstyle="round", tags="fg")


def make_icon(accent, size=64, hat=None):
    """Іконка програми (заокруглений квадрат із «$» у кольорі акценту); None, якщо немає Pillow."""
    if Image is None:
        return None
    try:
        from PIL import ImageDraw, ImageFont
        big = 256
        img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # із шапкою значок менший і зсунутий вниз, щоб вона помістилась
        hat = SANTA_HAT if hat is None else hat
        bx, by, side = (56, 80, 168) if hat else (8, 8, big - 16)
        d.rounded_rectangle((bx, by, bx + side, by + side), radius=side // 4, fill=accent)
        font = None
        for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
            try:
                font = ImageFont.truetype(name, int(side * 0.7))
                break
            except Exception:
                continue
        font = font or ImageFont.load_default()
        d.text((bx + side / 2, by + side / 2 + (12 if hat else 4)), "$",
               fill="#ffffff" if lum(accent) < 0.62 else "#111111", font=font, anchor="mm")
        if hat:
            k = side / 100
            for kind, color, pts in santa_hat_shapes():
                if kind == "poly":
                    d.polygon([(bx + x * k, by + y * k) for x, y in pts], fill=color)
                    continue
                x1, y1, x2, y2 = pts
                box = (bx + x1 * k, by + y1 * k, bx + x2 * k, by + y2 * k)
                if kind == "oval":
                    d.ellipse(box, fill=color, outline="#c9d3dc", width=3)
                else:
                    d.rounded_rectangle(box, radius=(box[3] - box[1]) / 2, fill=color, outline="#c9d3dc", width=3)
        return img.resize((size, size), Image.LANCZOS)
    except Exception:
        return None


def set_palette(theme, accent, palette="Класика", over=None):
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", accent or ""):
        accent = SWATCHES[0]
    pal = PALETTES.get(palette)
    if pal:
        theme = pal["theme"]
    P.clear()
    P.update(DARK if theme == "dark" else LIGHT)
    if pal:
        P.update({k: pal[k] for k in ("bg", "surf", "ink", "muted", "line")})
    # власні кольори фону / карток поверх палітри
    if isinstance(over, dict):
        changed = False
        for k in ("bg", "surf"):
            if re.fullmatch(r"#[0-9a-fA-F]{6}", over.get(k) or ""):
                P[k] = over[k]
                changed = True
        if changed:
            base = DARK if (lum(P["bg"]) + lum(P["surf"])) / 2 < 0.5 else LIGHT
            P["ink"], P["muted"] = base["ink"], base["muted"]
            P["line"] = mix(P["surf"], P["ink"], 0.14)
    P["theme"] = theme
    P["deco"] = pal.get("deco", "") if pal else ""
    P["acc"] = accent
    P["acc2"] = pal.get("acc2", accent) if pal else accent      # другий акцент (смужки)
    P["acc_d"] = mix(accent, "#000000", 0.25)
    P["acc_ink"] = "#ffffff" if lum(accent) < 0.62 else "#111111"
    if theme == "dark" and lum(accent) < 0.45:
        P["acc_text"] = mix(accent, "#ffffff", 0.45)
    elif theme != "dark" and lum(accent) > 0.6:
        P["acc_text"] = mix(accent, "#000000", 0.4)
    else:
        P["acc_text"] = accent
    P["sel"] = mix(P["surf"], accent, 0.16)


FONT = ("Segoe UI", 9)
FONT_B = ("Segoe UI Semibold", 9)       # підписи, кнопки-акценти
FONT_BIG = ("Segoe UI Semibold", 26)    # головна сума
FONT_NUM = ("Segoe UI Semibold", 18)    # друга цифра


def round_rect(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
           x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class Banner(tk.Canvas if tk else object):
    """Шапка з 3D-фоном: градієнт, підлога з перспективною сіткою та світіння в кольорі акценту."""

    def __init__(self, parent, title, height=68):
        super().__init__(parent, height=height, bg=P["bg"], highlightthickness=0, bd=0)
        self._title, self._h, self._chip = title, height, None
        self._st, self._tip = ("", "", "#f2994a"), None   # (короткий текст, повний текст, колір точки)
        self._phase, self._anim, self._anim_job = 0.0, False, None
        self._hl, self._geo = [], None                    # горизонтальні лінії підлоги та геометрія
        self._flakes, self._snow_on, self._snow_job, self._snow_w = [], False, None, 0
        self._waves, self._wave_job, self._vx = [], None, 0
        self._bulbs, self._stars, self._oc = [], [], []   # лампочки гірлянди, зорі, хвилі океану
        self.bind("<Configure>", lambda _e: self.draw())

    def set_animate(self, on):
        """Вмикає/вимикає повільний рух ліній підлоги до горизонту."""
        self._anim = bool(on)
        if self._anim:
            if self._anim_job is None:
                self._tick()
        else:
            if self._anim_job is not None:
                try:
                    self.after_cancel(self._anim_job)
                except Exception:
                    pass
                self._anim_job = None
            self._phase = 0.0
            self._apply_phase()

    def _tick(self):
        self._anim_job = None
        if not self._anim:
            return
        try:
            if self.winfo_viewable():       # у згорнутому вікні не малюємо
                self._phase = (self._phase + 0.008) % 1.0
                self._apply_phase()
            self._anim_job = self.after(40, self._tick)
        except tk.TclError:
            pass

    def _apply_phase(self):
        """Лінії підлоги: рівномірний зсув до горизонту, з перспективою (внизу швидше, ближче до обрію повільніше)."""
        if not self._geo:
            return
        w, h, hy = self._geo
        deco = P.get("deco", "")
        try:
            if deco == "xmas":
                return self._twinkle()
            if deco == "ocean":
                return self._wave_step(w, h)
        except tk.TclError:
            return
        if not self._hl:
            return
        n = len(self._hl) - 1
        bg, acc = P["bg"], P["acc"]
        try:
            for k, it in enumerate(self._hl):
                u = (k - self._phase) / n
                if u <= 0:
                    self.itemconfigure(it, state="hidden")
                    continue
                y = hy + (h - hy) * u * u
                self.coords(it, 0, y, w, y)
                self.itemconfigure(it, state="normal", fill=mix(bg, acc, 0.10 + 0.32 * u * u))
        except tk.TclError:
            pass

    def _twinkle(self):
        """Новорічна шапка: мерехтіння лампочок гірлянди та зірок."""
        bg, ph = P["bg"], self._phase * 6.2832
        for k, (it, glow, col) in enumerate(self._bulbs):
            f = 0.55 + 0.45 * math.sin(ph * 2 + k * 1.3)
            self.itemconfigure(it, fill=mix(bg, col, 0.35 + 0.65 * f))
            self.itemconfigure(glow, fill=mix(bg, col, 0.30 * f))
        for k, (it, base) in enumerate(self._stars):
            f = 0.5 + 0.5 * math.sin(ph * 3 + k * 2.1)
            self.itemconfigure(it, fill=mix(bg, "#ffffff", base * (0.30 + 0.70 * f)))

    def _wave_step(self, w, h):
        """Океан: хвилі повільно пливуть (ціле число періодів за цикл — без стрибка)."""
        for it, base, amp, wl, k in self._oc:
            pts = []
            for x in range(-12, int(w) + 25, 12):
                pts += [x, base + amp * math.sin(6.2832 * (x / wl + k * self._phase))]
            pts += [w + 24, h + 10, -12, h + 10]
            self.coords(it, *pts)

    def _hill(self, w, h, base_y, amp, wl, ph, color):
        pts = []
        for x in range(-20, int(w) + 41, 20):
            pts += [x, base_y + amp * math.sin(x / wl + ph)]
        pts += [w + 40, h + 12, -20, h + 12]
        self.create_polygon(pts, smooth=True, fill=color, outline="")

    def _scene_xmas(self, w, h):
        bg = P["bg"]
        rnd = random.Random(11)
        self._stars = []
        for _ in range(max(10, int(w / 24))):                      # зорі
            x, y = rnd.uniform(0, w), rnd.uniform(2, h * 0.52)
            r, base = rnd.choice((0.8, 0.8, 1.1, 1.5)), rnd.uniform(0.55, 1.0)
            if x < 150 and y > 18:
                continue
            it = self.create_oval(x - r, y - r, x + r, y + r, fill=mix(bg, "#ffffff", base * 0.6), outline="")
            self._stars.append((it, base))
        self._hill(w, h, h - 14, 3, 60, 0.5, mix(bg, "#9fc7b8", 0.32))      # задній пагорб
        x = 175 + rnd.uniform(0, 20)
        while x < w - 260:                                          # ялинки зі сніжними шапками
            ht = rnd.uniform(20, 28)
            base_y = h - 9
            self.create_rectangle(x - 1.5, base_y - 3, x + 1.5, base_y + 1, fill="#5b3a29", outline="")
            for i in range(3):
                yt = base_y - ht + i * ht * 0.27
                yb = yt + ht * 0.42
                half = ht * 0.32 * (0.55 + 0.30 * i)
                self.create_polygon(x, yt, x + half, yb, x - half, yb, fill=mix(bg, "#2fa36b", 0.80), outline="")
                self.create_polygon(x, yt, x + half * 0.55, yt + (yb - yt) * 0.55,
                                    x - half * 0.55, yt + (yb - yt) * 0.55, fill="#f4faf7", outline="")
            self.create_oval(x - 2, base_y - ht - 2, x + 2, base_y - ht + 2, fill="#ffd166", outline="")
            for dx, dy, col in ((-3, 0.55, "#ff4d5e"), (4, 0.78, "#ffd166"), (-4, 0.86, "#5ab0ff")):
                self.create_oval(x + dx - 1.4, base_y - ht + ht * dy - 1.4, x + dx + 1.4,
                                 base_y - ht + ht * dy + 1.4, fill=col, outline="")
            x += rnd.uniform(58, 84)
        self._hill(w, h, h - 8, 2.5, 45, 2.0, "#eaf3ef")             # сніговий пагорб
        # гірлянда з лампочками
        self._bulbs = []
        cols = ["#ff4d5e", "#ffd166", "#3ddc84", "#5ab0ff"]
        sw, k = 110, 0
        for i in range(int(w // sw) + 1):
            x0, x1 = i * sw, min((i + 1) * sw, w)
            if x1 - x0 < 20:
                continue
            curve = lambda t: (x0 + (x1 - x0) * t, 2 + 9 * math.sin(math.pi * t) * ((x1 - x0) / sw))
            pts = [v for t in (j / 10 for j in range(11)) for v in curve(t)]
            self.create_line(*pts, fill=mix(bg, "#2e7d57", 0.65), width=1, smooth=True)
            for t in (0.14, 0.38, 0.62, 0.86):
                bx, by = curve(t)
                by += 3.5
                col = cols[k % 4]
                glow = self.create_oval(bx - 6, by - 6, bx + 6, by + 6, fill=mix(bg, col, 0.25), outline="")
                it = self.create_oval(bx - 2.4, by - 2.4, bx + 2.4, by + 2.4, fill=col, outline="")
                self._bulbs.append((it, glow, col))
                k += 1

    def _scene_neon(self, w, h, hy, vx):
        bg, acc, acc2 = P["bg"], P["acc"], P["acc2"]
        r = h * 0.30
        for m, f in ((1.9, 0.07), (1.5, 0.13), (1.2, 0.22)):        # світіння навколо сонця
            self.create_oval(vx - r * m * 1.6, hy - r * m * 0.9, vx + r * m * 1.6, hy + r * m * 0.9,
                             fill=mix(bg, acc, f), outline="")
        rows = 12
        for i in range(rows):                                        # ретро-сонце: смуги, що тоншають донизу
            t = i / rows
            y0 = hy - r + t * r
            y1 = y0 + (r / rows) * (1 - 0.6 * t)
            half = math.sqrt(max(r * r - (hy - (y0 + y1) / 2) ** 2, 0))
            self.create_rectangle(vx - half, y0, vx + half, y1, fill=mix(acc2, acc, t), outline="")
        self._hl = [self.create_line(0, hy, w, hy, fill=bg, tags="hz") for _ in range(7)]
        self._apply_phase()
        for i in range(-14, 15):
            self.create_line(vx, hy, vx + i * w / 9, h, fill=mix(bg, acc, 0.26))

    def _scene_graphite(self, w, h):
        bg, acc = P["bg"], P["acc"]
        for x in range(0, int(w), 24):                               # креслярська сітка
            self.create_line(x, 0, x, h, fill=mix(bg, acc, 0.05))
        for y in range(0, h, 12):
            self.create_line(0, y, w, y, fill=mix(bg, acc, 0.04))
        x0 = max(170, w - 340)
        n = int((w - 170 - x0) // 19)
        for i in range(max(n, 0)):                                   # стовпчики зростання
            bh = 6 + (h - 16) * (i + 1) / max(n, 1) * (0.75 + 0.25 * ((i * 7) % 3) / 2)
            self.create_rectangle(x0 + i * 19, h - 2 - bh, x0 + i * 19 + 12, h - 2,
                                  fill=mix(bg, acc, 0.10 + 0.03 * i), outline="")

    def _scene_ocean(self, w, h):
        bg, acc = P["bg"], P["acc"]
        self._oc = []
        for base, amp, wl, k, f in ((0.78, 3, 120, 1, 0.16), (0.86, 4, 90, -1, 0.28), (0.94, 3, 160, 2, 0.42)):
            it = self.create_polygon(0, 0, 1, 0, 1, 1, 0, 1, smooth=True, fill=mix(bg, acc, f), outline="")
            self._oc.append((it, h * base, amp, wl, k))
        for i in range(3):                                           # слабкі промені світла зверху
            x = w * (0.18 + 0.3 * i)
            self.create_polygon(x, 0, x + 26, 0, x + 70, h, x + 20, h, fill=mix(bg, acc, 0.05), outline="")

    def _scene_mint(self, w, h):
        bg, acc, acc2 = P["bg"], P["acc"], P["acc2"]
        sx, sy = int(w * 0.68), 15
        for rr, f in ((24, 0.10), (17, 0.22), (11, 0.40)):          # сонце з ореолом
            self.create_oval(sx - rr, sy - rr, sx + rr, sy + rr, fill=mix(bg, acc2, f), outline="")
        self.create_oval(sx - 7, sy - 7, sx + 7, sy + 7, fill=mix(acc2, "#ffffff", 0.25), outline="")
        self._hill(w, h, h - 16, 4, 70, 0.5, mix(bg, acc, 0.26))
        rnd = random.Random(5)
        x = 175 + rnd.uniform(0, 25)
        while x < w - 260:                                           # круглі деревця
            self.create_line(x, h - 14, x, h - 20, fill="#7a5c3e", width=2)
            self.create_oval(x - 6, h - 31, x + 6, h - 18, fill=mix(bg, acc, 0.70), outline="")
            x += rnd.uniform(60, 90)
        self._hill(w, h, h - 10, 3, 50, 2.0, mix(bg, acc, 0.46))

    # --- сніг ---
    def set_snow(self, on):
        self._snow_on = bool(on)
        if self._snow_on:
            if self._snow_job is None:
                self._snow_tick()
        else:
            if self._snow_job is not None:
                try:
                    self.after_cancel(self._snow_job)
                except Exception:
                    pass
                self._snow_job = None
            self._flakes = []
            try:
                self.delete("snow")
            except tk.TclError:
                pass

    def _snow_tick(self):
        self._snow_job = None
        if not self._snow_on:
            return
        try:
            if self.winfo_viewable():       # у згорнутому вікні не малюємо
                self._snow_step(max(self.winfo_width(), 200), self._h)
            self._snow_job = self.after(45, self._snow_tick)
        except tk.TclError:
            pass

    def _snow_step(self, w, h):
        if not self._flakes:
            kind = P.get("deco", "")
            n_ = {"ocean": 16, "neon": 22, "mint": 18}.get(kind, 0) or max(24, min(70, w // 12))
            for _ in range(n_):
                r = random.uniform(1.0, 2.6)
                if kind == "neon":
                    r = random.uniform(0.9, 1.6)
                # [x, y, радіус, швидкість падіння, розмах хитання, фаза, id на полотні]
                self._flakes.append([random.uniform(0, w), random.uniform(0, h), r, 0.5 + 0.4 * r,
                                     random.uniform(0.3, 1.0), random.uniform(0, 6.28), None, random.random()])
            self._snow_w = w
        elif abs(w - self._snow_w) > 2:
            k = w / self._snow_w
            for f in self._flakes:
                f[0] *= k
            self._snow_w = w
        mode = {"xmas": "snow", "ocean": "bubble", "neon": "spark", "mint": "pollen"}.get(P.get("deco", ""))
        if mode is None:
            return
        base = "#ffffff" if P.get("theme") == "dark" else mix(P["acc"], "#ffffff", 0.35)
        rising = mode != "snow"          # бульбашки, іскри і пилок підіймаються, сніг падає
        for f in self._flakes:
            f[1] += -f[3] if rising else f[3]
            f[5] += 0.04
            f[0] += math.sin(f[5]) * 0.45 * f[4]
            if (rising and f[1] < -4) or (not rising and f[1] > h + 3):
                f[1], f[0] = (h + 4.0 if rising else -3.0), random.uniform(0, w)
            x, y, r = f[0], f[1], f[2]
            if f[6] is None:
                if mode == "snow":
                    col = mix(P["bg"], base, 0.45 + 0.5 * (r - 1.0) / 1.6)
                    f[6] = self.create_oval(x - r, y - r, x + r, y + r, fill=col, outline="", tags="snow")
                elif mode == "bubble":
                    r = f[2] = r * 1.4
                    f[6] = self.create_oval(x - r, y - r, x + r, y + r, fill="", tags="snow",
                                            outline=mix(P["bg"], "#bfefff", 0.30 + 0.35 * random.random()))
                elif mode == "spark":
                    col = P["acc"] if f[7] < 0.5 else P["acc2"]
                    f[6] = self.create_oval(x - r * .8, y - r * .8, x + r * .8, y + r * .8, tags="snow",
                                            fill=mix(P["bg"], col, 0.55 + 0.4 * f[7]), outline="")
                else:   # пилок
                    f[6] = self.create_oval(x - r, y - r, x + r, y + r, tags="snow", outline="",
                                            fill=mix(P["bg"], P["acc2"] if f[7] < 0.35 else P["acc"], 0.55 + 0.35 * f[7]))
            else:
                self.coords(f[6], x - f[2], y - f[2], x + f[2], y + f[2])

    # --- хвиля при новій оплаті: кільце розходиться підлогою від точки сходу ---
    def ripple(self):
        self._waves.append(time.monotonic())
        if self._wave_job is None:
            self._ripple_tick()

    def _ripple_tick(self):
        self._wave_job = None
        try:
            now, dur = time.monotonic(), 1.6
            self._waves = [t for t in self._waves if now - t < dur]
            self.delete("wave")
            if not self._waves or not self._geo:
                return
            w, h, hy = self._geo
            cx, cy = self._vx, hy + (h - hy) * 0.35
            far = mix(P["bg"], P["acc"], 0.12)
            for t0 in self._waves:
                for off in (0.0, 0.14):
                    u = (now - t0) / dur - off
                    if 0 < u < 1:
                        r = w * 0.8 * (1 - (1 - u) ** 2)
                        self.create_oval(cx - r, cy - r * 0.34, cx + r, cy + r * 0.34,
                                         outline=mix(far, P["acc"], 0.95 * (1 - u)),
                                         width=3 if u < 0.4 else (2 if u < 0.7 else 1), tags="wave")
            try:
                self.tag_lower("wave", "fg")
            except tk.TclError:
                pass
            self._wave_job = self.after(33, self._ripple_tick)
        except tk.TclError:
            pass

    def set_status(self, short, full, color):
        self._st = (short, full, color)
        self.draw()

    def _show_tip(self, show):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None
        if show and self._st[1]:
            t = tk.Toplevel(self)
            t.overrideredirect(True)
            t.configure(bg=P["line"])
            tk.Label(t, text=self._st[1], bg=P["surf"], fg=P["ink"], font=("Segoe UI", 8), padx=8, pady=4).pack(padx=1, pady=1)
            t.geometry(f"+{self.winfo_pointerx() + 10}+{self.winfo_pointery() + 16}")
            self._tip = t

    def _draw_status(self, w, h):
        short, _full, color = self._st
        if not short:
            return
        txt = self.create_text(w - 14, h / 2, text=short, anchor="e", fill=mix(P["muted"], P["ink"], 0.45),
                               font=("Segoe UI", 8), tags="st")
        x1 = self.bbox(txt)[0]
        self.create_oval(x1 - 12, h / 2 - 3, x1 - 6, h / 2 + 3, fill=color, outline="", tags="st")
        self.tag_bind("st", "<Enter>", lambda _e: self._show_tip(True))
        self.tag_bind("st", "<Leave>", lambda _e: self._show_tip(False))

    def attach(self, widget):
        self._chip = self.create_window(0, 0, window=widget, anchor="e")
        self.draw()

    def confetti(self):
        """Коротке конфетті в шапці (план виконано)."""
        import random
        try:
            w, h = max(self.winfo_width(), 200), self._h
            colors = (["#d62839", "#f2c14e", "#2e9e5b", "#ffffff"] if P.get("deco") == "xmas"
                      else [P["acc"], P["acc2"], "#f2b84b", "#27ae60", "#ffffff", "#2f80ed"])
            parts = []
            for _ in range(46):
                x, y, sz = random.uniform(0, w), random.uniform(-h, 0), random.randint(3, 6)
                it = self.create_rectangle(x, y, x + sz, y + sz * 0.6, fill=random.choice(colors), outline="", tags="confetti")
                parts.append([it, random.uniform(-1.4, 1.4), random.uniform(2.0, 4.5)])

            def tick(n):
                try:
                    for p_ in parts:
                        self.move(p_[0], p_[1], p_[2])
                        p_[2] += 0.18
                    if n < 60:
                        self.after(28, tick, n + 1)
                    else:
                        self.delete("confetti")
                except tk.TclError:
                    pass
            tick(0)
        except tk.TclError:
            pass

    def draw(self):
        w, h = max(self.winfo_width(), 200), self._h
        bg, acc = P["bg"], P["acc"]
        chip = self._chip
        for it in self.find_all():
            if it != chip:
                self.delete(it)
        for f in self._flakes:      # всі елементи видалено: сніжинки створяться знову на наступному кадрі
            f[6] = None
        deco = P.get("deco", "")
        self._hl, self._bulbs, self._stars, self._oc = [], [], [], []
        # градієнт зверху вниз: від фону до легкого відтінку (у новорічної — ялинково-зелений)
        tint = "#1f8a5f" if deco == "xmas" else acc
        k_ = {"xmas": 0.30, "graphite": 0.10, "mint": 0.28, "ocean": 0.30}.get(deco, 0.20)
        for y in range(0, h, 2):
            self.create_rectangle(0, y, w, y + 2, fill=mix(bg, tint, k_ * (y / h) ** 1.6), outline="")
        hy, vx = int(h * 0.34), int(w * 0.68)
        self._geo = (w, h, hy)
        self._vx = vx
        if deco == "xmas":
            self._scene_xmas(w, h)
        elif deco == "neon":
            self._scene_neon(w, h, hy, vx)
        elif deco == "graphite":
            self._scene_graphite(w, h)
        elif deco == "ocean":
            self._scene_ocean(w, h)
        elif deco == "mint":
            self._scene_mint(w, h)
        else:
            # підлога: сітка у перспективі зі спільною точкою сходу
            self._hl = [self.create_line(0, hy, w, hy, fill=bg, tags="hz") for _ in range(7)]
            for i in range(-14, 15):
                self.create_line(vx, hy, vx + i * w / 9, h, fill=mix(bg, acc, 0.22))
            # світіння на горизонті
            for r_ in range(46, 3, -3):
                self.create_oval(vx - r_ * 1.7, hy - r_ * 0.55, vx + r_ * 1.7, hy + r_ * 0.55,
                                 fill=mix(bg, acc, 0.03 + 0.55 * (1 - r_ / 46) ** 2), outline="")
        self._apply_phase()
        self.create_line(0, h - 1, w, h - 1, fill=P["acc2"] if P["acc2"] != acc else P["line"])
        # логотип і назва
        round_rect(self, 14, (h - 30) / 2, 44, (h + 30) / 2, 9, fill=acc, outline="", tags="fg")
        self.create_text(29, h / 2, text="$", fill=P["acc_ink"], font=("Segoe UI Semibold", 13), tags="fg")
        if deco == "neon":      # неонове сяйво навколо назви
            for dx, dy in ((-1.5, 0), (1.5, 0), (0, -1.5), (0, 1.5)):
                self.create_text(56 + dx, h / 2 + dy, text=self._title, anchor="w", fill=mix(bg, acc, 0.45),
                                 font=("Segoe UI Semibold", 16), tags="fg")
        self.create_text(56, h / 2, text=self._title, anchor="w", fill=P["ink"], font=("Segoe UI Semibold", 16), tags="fg")
        if xmas():
            draw_hat(self, 14, (h - 30) / 2, 30)
        if chip is not None:
            self.coords(chip, w - 14, h / 2)
            self.tag_raise(chip)
        self._draw_status(w, h)
        try:
            self.tag_raise("snow")
        except tk.TclError:
            pass


class RoundBtn(tk.Canvas if tk else object):
    """Кнопка із заокругленими кутами; приймає ті самі configure(bg/fg/…), що й звичайна tk.Button."""

    def __init__(self, parent, text, command, font=FONT, padx=14, height=30, radius=9):
        try:
            pbg = parent.cget("bg")
        except tk.TclError:
            pbg = P["bg"]
        self._font = tkfont.Font(font=font)
        self._padx, self._r = padx, radius
        super().__init__(parent, width=self._font.measure(text) + 2 * padx, height=height, bg=pbg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._label, self._cmd, self._down = text, command, False
        self._c = {"bg": P["surf"], "fg": P["ink"], "active": P["sel"], "afg": P["ink"], "border": P["line"]}
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)

    def configure(self, cnf=None, **kw):
        kw = dict(cnf or {}, **kw)
        keymap = {"bg": "bg", "background": "bg", "fg": "fg", "foreground": "fg", "activebackground": "active",
                  "activeforeground": "afg", "highlightbackground": "border"}
        for k in list(kw):
            if k in keymap:
                self._c[keymap[k]] = kw.pop(k)
        if "text" in kw:
            self._label = kw.pop("text")
            kw["width"] = self._font.measure(self._label) + 2 * self._padx
        for k in ("relief", "bd", "padx", "pady", "highlightthickness", "command"):
            kw.pop(k, None)
        if kw:
            super().configure(**kw)
        self._draw()

    config = configure

    def _draw(self):
        try:
            w = self.winfo_width() if self.winfo_width() > 1 else self.winfo_reqwidth()
            h = self.winfo_height() if self.winfo_height() > 1 else self.winfo_reqheight()
            self.delete("all")
            fill = self._c["active"] if self._down else self._c["bg"]
            fg = self._c["afg"] if self._down else self._c["fg"]
            round_rect(self, 1, 1, w - 1, h - 1, self._r, fill=fill, outline=self._c["border"])
            self.create_text(w / 2, h / 2, text=self._label, fill=fg, font=self._font)
        except tk.TclError:
            pass

    def _press(self, _e):
        self._down = True
        self._draw()

    def _release(self, e):
        self._down = False
        self._draw()
        if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self._cmd()


def _hover(b, inside):
    try:
        on = getattr(b, "_on", False)
        if inside:
            b.configure(bg=P["acc_d"] if on else P["sel"])
        else:
            b.configure(bg=P["acc"] if on else P["surf"])
    except tk.TclError:
        pass


def make_btn(parent, text, cmd, primary=False, height=30, font=None):
    b = RoundBtn(parent, text, cmd, font=font or (FONT_B if primary else FONT), height=height)
    paint_toggle(b, primary)
    b.bind("<Enter>", lambda _e: _hover(b, True))
    b.bind("<Leave>", lambda _e: _hover(b, False))
    return b


def make_entry(parent, **kw):
    return tk.Entry(parent, relief="flat", font=FONT, bg=P["surf"], fg=P["ink"], insertbackground=P["ink"],
                    highlightthickness=1, highlightbackground=P["line"], highlightcolor=P["acc"], **kw)


def paint_toggle(b, on):
    b._on = on
    b.configure(bg=P["acc"] if on else P["surf"], fg=P["acc_ink"] if on else P["ink"],
                activebackground=P["acc_d"] if on else P["sel"],
                activeforeground=P["acc_ink"] if on else P["ink"],
                highlightbackground=P["acc"] if on else P["line"])


def stat_card(parent, items, earn=None):
    card = tk.Frame(parent, bg=P["surf"], highlightthickness=1, highlightbackground=P["line"])
    ncols = len(items) * 2 - 1
    tk.Frame(card, bg=P["acc"], height=3).grid(row=0, column=0, columnspan=ncols, sticky="we")
    for i, (cap, var, color) in enumerate(items):
        col = i * 2
        if i:
            tk.Frame(card, bg=P["line"], width=1).grid(row=1, column=col - 1, sticky="ns", pady=10)
        card.columnconfigure(col, weight=1)
        cell = tk.Frame(card, bg=P["surf"])
        cell.grid(row=1, column=col, sticky="w", padx=14, pady=(8, 6))
        tk.Label(cell, text=cap.upper(), bg=P["surf"], fg=P["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(cell, textvariable=var, bg=P["surf"], fg=color, font=("Segoe UI", 19, "bold")).pack(anchor="w")
    if earn:
        cap_var, val_var = earn
        row = tk.Frame(card, bg=P["surf"])
        row.grid(row=2, column=0, columnspan=ncols, sticky="we", padx=14, pady=(0, 8))
        tk.Label(row, textvariable=cap_var, bg=P["surf"], fg=P["muted"], font=("Segoe UI", 8)).pack(side="left")
        tk.Label(row, textvariable=val_var, bg=P["surf"], fg=P["acc_text"], font=("Segoe UI", 11, "bold")).pack(side="right")
    return card


STATE_COLOR = {"ok": "#27ae60", "busy": "#f2994a", "err": "#e5484d"}


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.cfg = load_cfg()
        apply_groups(self.cfg.get("GROUPS"))
        set_palette(self.cfg.get("THEME", "light"), self.cfg.get("ACCENT", SWATCHES[0]), self.cfg.get("PALETTE", "Класика"),
                    self.cfg.get("COLORS"))
        self.client = None
        self.entity = None
        self.ready = False
        self.payments = []
        self.day = ""
        self.row_models = {}
        self._hover_iid, self._hover_tags = None, ()
        self.range = None  # (початок, кінець) для перегляду за довільний період по годинах
        self.popups = []
        self.seen_ids = set()
        self.channel_title = ""
        self.status_state = "busy"
        self.status_text = ""
        self.group = self.cfg.get("GROUP") or "2"
        if self.group != "all" and self.group not in GROUPS:
            self.group = group_keys()[0]
        self.nosub = tk.BooleanVar(value=True)
        self.shifts = (set(self.cfg.get("SHIFTS") or []) & ALL_SHIFTS) or set(ALL_SHIFTS)
        self.date_var = tk.StringVar(value=datetime.now(TZ).date().isoformat())
        self.v_cnt, self.v_net = tk.StringVar(value="0"), tk.StringVar(value="$0.00")
        self.last_text = ""
        self._last_pay = None   # остання оплата у поточному виборі (для рядка під сумою)
        self.mcache, self.mfail, self.mloading = {}, None, None  # оплати місяця для плану «Місяць»
        self.plan_frac = 0.0
        self._net_val, self._net_job = 0.0, None
        self._bar_val, self._bar_job = 0.0, None
        self._frac_seen = {}
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        root.title("Оплати")
        geo = self.cfg.get("GEOMETRY", "")
        ok_geo = re.fullmatch(r"(\d+)x(\d+)(\+-?\d+\+-?\d+)?", geo)
        root.geometry(geo if ok_geo and int(ok_geo.group(1)) >= 440 and int(ok_geo.group(2)) >= 420 else "480x660")
        root.minsize(440, 420)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.build()
        self.poll()
        self.root.after(15000, self._last_tick)
        self.root.after(200, self.start)
        self.root.after(8000, cleanup_update_leftovers)
        if self.cfg.get("AUTO_UPDATE", True):
            self.root.after(6000, lambda: self.check_update(False))

    # --- налаштування за замовчуванням ---
    def notify_cfg(self):
        n = {k: True for k, _, _ in TYPES}
        n.update(self.cfg.get("NOTIFY", {}))
        return n

    # --- побудова вікна ---
    def build(self):
        r = self.root
        r.configure(bg=P["bg"])
        # --- шапка з 3D-фоном: назва і стан підключення ---
        banner = Banner(r, "Оплати")
        banner.pack(fill="x")
        self.banner = banner
        banner.set_animate(self.cfg.get("ANIM", True))
        banner.set_snow(self.cfg.get("SNOW", True))
        banner.set_status(short_status(self.status_text), self.status_text, STATE_COLOR.get(self.status_state, "#f2994a"))

        # --- головна картка: сума, кількість і план ---
        hero = tk.Frame(r, bg=P["surf"], highlightthickness=1, highlightbackground=P["line"])
        hero.pack(fill="x", padx=14, pady=(10, 0))
        dark_ui = P.get("theme") == "dark"
        for pad, k in ((3, 0.30 if dark_ui else 0.06), (8, 0.55 if dark_ui else 0.12)):
            tk.Frame(r, bg=mix(P["bg"], "#000000", k), height=2).pack(fill="x", padx=14 + pad)
        tk.Frame(hero, bg=P["acc2"], height=3).pack(fill="x")
        nums = tk.Frame(hero, bg=P["surf"])
        nums.pack(fill="x", padx=16, pady=(8, 0))
        left = tk.Frame(nums, bg=P["surf"])
        left.pack(side="left")
        tk.Label(left, text="Чистими", bg=P["surf"], fg=P["muted"], font=FONT).pack(anchor="w")
        self.net_lbl = tk.Label(left, textvariable=self.v_net, bg=P["surf"], fg=P["acc_text"], font=FONT_BIG)
        self.net_lbl.pack(anchor="w")
        lrow = tk.Frame(left, bg=P["surf"])
        lrow.pack(anchor="w", pady=(0, 2))
        self.last_a = tk.Label(lrow, text="", bg=P["surf"], fg=P["acc_text"], font=FONT_B)
        self.last_a.pack(side="left")
        self.last_b = tk.Label(lrow, text="", bg=P["surf"], fg=P["muted"], font=FONT)
        self.last_b.pack(side="left")
        # кільце плану навколо кількості платежів
        self.ring = tk.Canvas(nums, width=RING, height=RING, bg=P["surf"], highlightthickness=0, bd=0)
        self.ring.pack(side="right", pady=(0, 2))
        tk.Frame(hero, bg=P["line"], height=1).pack(fill="x", padx=16, pady=(6, 6))
        plan = tk.Frame(hero, bg=P["surf"])
        plan.pack(fill="x", padx=16, pady=(0, 8))
        prow = tk.Frame(plan, bg=P["surf"])
        prow.pack(fill="x")
        tk.Label(prow, text="План", bg=P["surf"], fg=P["ink"], font=FONT_B).pack(side="left", padx=(0, 8))
        self.pbtns = {}
        for key, name in PLAN_MODES.items():
            b = make_btn(prow, name, lambda k=key: self.set_plan_mode(k), height=26)
            b.pack(side="left", padx=(0, 4))
            self.pbtns[key] = b
        make_btn(prow, "Оновити", lambda: self.refresh_plan(False), height=26).pack(side="right")
        self.plan_lbl = tk.Label(plan, text="", bg=P["surf"], fg=P["ink"], font=FONT, justify="left", anchor="w", height=2)
        self.plan_lbl.pack(fill="x", pady=(6, 0))
        self.plan_lbl.bind("<Configure>", lambda e: self.plan_lbl.configure(wraplength=max(e.width - 4, 100)))
        self.paint_plan()
        self.draw_bar()
        self.refresh_last()

        # --- керування (компактно): група · дата · зміни, період згортається ---
        ctl = tk.Frame(r, bg=P["bg"])
        ctl.pack(fill="x", padx=14, pady=(8, 2))

        grow = tk.Frame(ctl, bg=P["bg"])
        grow.pack(fill="x", pady=2)
        self.gbtns = {}
        for g in group_keys() + ["all"]:
            b = make_btn(grow, "Усі" if g == "all" else g, lambda g=g: self.set_group(g), height=28)
            b.pack(side="left", padx=(0, 4))
            self.gbtns[g] = b
        make_btn(grow, "✎ Групи", self.open_groups, height=28).pack(side="right")
        self.paint_groups()

        drow = tk.Frame(ctl, bg=P["bg"])
        drow.pack(fill="x", pady=2)
        make_btn(drow, "Сьогодні", lambda: self.pick(0), height=28).pack(side="left", padx=(0, 4))
        make_btn(drow, "Вчора", lambda: self.pick(1), height=28).pack(side="left", padx=(0, 4))
        e = make_entry(drow, textvariable=self.date_var, width=11, justify="center")
        e.pack(side="left", padx=(0, 4), ipady=4)
        e.bind("<Return>", lambda _e: self.fetch(self.date_var.get()))
        make_btn(drow, "Порахувати", lambda: self.fetch(self.date_var.get()), True, height=28).pack(side="left")

        srow = tk.Frame(ctl, bg=P["bg"])
        srow.pack(fill="x", pady=2)
        self.sbtns = {}
        for key, name, span, _a, _b in SHIFT_DEFS:
            b = make_btn(srow, f"{name} {span}", lambda k=key: self.toggle_shift(k), height=28)
            b.pack(side="left", padx=(0, 4))
            self.sbtns[key] = b
        self.paint_shifts()
        self.pbtn = make_btn(srow, "Період", self.toggle_period, height=28)
        self.pbtn.pack(side="right")

        # довільний період по годинах: показується кнопкою «Період»
        self.pframe = tk.Frame(ctl, bg=P["bg"])
        self.r_from, self.r_to = tk.StringVar(), tk.StringVar()
        prow2 = tk.Frame(self.pframe, bg=P["bg"])
        prow2.pack(fill="x", pady=2)
        for var in (self.r_from, self.r_to):
            e2 = make_entry(prow2, textvariable=var, width=13, justify="center")
            e2.pack(side="left", padx=(0, 4), ipady=4)
            e2.bind("<Return>", lambda _e: self.fetch_period())
            if var is self.r_from:
                tk.Label(prow2, text="–", bg=P["bg"], fg=P["muted"], font=FONT).pack(side="left", padx=(0, 4))
        make_btn(prow2, "Показати", self.fetch_period, height=28).pack(side="left")
        tk.Label(self.pframe, text="напр. 14.11 08:00 – 16.11 22:00 (зміни тут не діють)", bg=P["bg"], fg=P["muted"],
                 font=("Segoe UI", 8)).pack(anchor="w")
        self.paint_period_panel(srow)

        nrow = tk.Frame(r, bg=P["bg"])
        nrow.pack(fill="x", padx=14, pady=(0, 2))
        tk.Checkbutton(nrow, text="Без підписок", variable=self.nosub, command=self.render,
                       bg=P["bg"], activebackground=P["bg"], fg=P["ink"], activeforeground=P["ink"],
                       selectcolor=P["surf"], font=FONT, highlightthickness=0, bd=0).pack(side="left")
        self.notes = tk.Label(nrow, text="", bg=P["bg"], fg=P["muted"], font=("Segoe UI", 8), justify="right", anchor="e")
        self.notes.pack(side="right")

        # нижня панель пакується першою з side="bottom", щоб кнопки не зникали при зменшенні вікна
        bot = tk.Frame(r, bg=P["bg"])
        bot.pack(side="bottom", fill="x", padx=14, pady=(6, 10))
        tk.Frame(r, bg=P["line"], height=1).pack(side="bottom", fill="x")
        make_btn(bot, "Копіювати", self.copy, True, height=34).pack(side="left")
        make_btn(bot, "Заробіток", self.calc_chatter, height=34).pack(side="left", padx=(6, 0))
        make_btn(bot, "⚙", self.open_settings, height=34, font=("Segoe UI Symbol", 13)).pack(side="right")
        make_btn(bot, "☀" if P.get("theme") == "dark" else "☾", self.toggle_theme, height=34,
                 font=("Segoe UI Symbol", 13)).pack(side="right", padx=(0, 6))

        st = ttk.Style()
        st.theme_use("clam")
        st.configure("Treeview", background=P["surf"], fieldbackground=P["surf"], foreground=P["ink"], rowheight=28,
                     borderwidth=0, font=("Segoe UI", 10))
        st.configure("Treeview.Heading", background=P["surf"], foreground=P["muted"], relief="flat",
                     font=("Segoe UI Semibold", 9), padding=(8, 8))
        st.map("Treeview", background=[("selected", P["sel"])], foreground=[("selected", P["ink"])])
        st.map("Treeview.Heading", background=[("active", P["surf"])])
        st.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        st.configure("Vertical.TScrollbar", background=P["line"], troughcolor=P["surf"], bordercolor=P["surf"],
                     arrowcolor=P["muted"], lightcolor=P["line"], darkcolor=P["line"])
        style_titlebar(self.root)
        self.set_icon()
        tf = tk.Frame(r, bg=P["surf"], highlightthickness=1, highlightbackground=P["line"])
        tf.pack(fill="both", expand=True, padx=14, pady=(2, 0))
        self.tree = ttk.Treeview(tf, columns=("name", "cnt", "net", "bw", "para"), show="headings", selectmode="browse")
        for col, text, w, anc in (("name", "Анкета", 140, "w"), ("cnt", "Плат.", 50, "e"),
                                  ("net", "Чистими", 90, "e"),
                                  ("bw", "BigWish", 72, "e"), ("para", "Para", 72, "e")):
            self.tree.heading(col, text=text, anchor=anc)
            self.tree.column(col, width=w, minwidth=min(w // 2, 56), anchor=anc, stretch=(col == "name"))
        self.tree.tag_configure("grp", background=P["sel"], foreground=P["acc_text"], font=("Segoe UI Semibold", 10))
        self.tree.tag_configure("odd", background=mix(P["surf"], P["bg"], 0.55))
        self.tree.tag_configure("hover", background=mix(P["surf"], P["acc"], 0.18))
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Motion>", self.on_tree_motion)
        self.tree.bind("<Leave>", lambda _e: self.set_hover(None))
        self.tree.bind("<Double-1>", self.on_row)
        self.tree.bind("<Return>", self.on_row)


    def rebuild(self):
        for w in self.root.winfo_children():
            if not isinstance(w, tk.Toplevel):
                w.destroy()
        self.build()
        self.render()

    def apply_theme(self):
        set_palette(self.cfg.get("THEME", "light"), self.cfg.get("ACCENT", SWATCHES[0]), self.cfg.get("PALETTE", "Класика"),
                    self.cfg.get("COLORS"))
        self.rebuild()

    def _save(self):
        try:
            save_cfg(self.cfg)
        except Exception:
            pass

    def toggle_theme(self):
        self.cfg["THEME"] = "light" if P.get("theme") == "dark" else "dark"
        self.cfg["PALETTE"] = "Класика"
        self.cfg.pop("COLORS", None)
        try:
            save_cfg(self.cfg)
        except Exception:
            pass
        self.apply_theme()

    def paint_groups(self):
        for g, b in self.gbtns.items():
            paint_toggle(b, g == self.group)

    def paint_shifts(self):
        for k, b in self.sbtns.items():
            paint_toggle(b, k in self.shifts)

    def toggle_shift(self, k):
        if k in self.shifts:
            if len(self.shifts) == 1:  # хоча б одна зміна завжди лишається обраною
                return
            self.shifts.discard(k)
        else:
            self.shifts.add(k)
        self.cfg["SHIFTS"] = sorted(self.shifts)
        try:
            save_cfg(self.cfg)
        except Exception:
            pass
        self.paint_shifts()
        self.render()

    # --- потік подій ---
    def poll(self):
        # помилка в одному завданні більше не зупиняє весь цикл (а з ним і сповіщення)
        try:
            while True:
                try:
                    fn = self.q.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn()
                except Exception:
                    log_error("poll")
        finally:
            self.root.after(100, self.poll)

    def run(self, coro, cb):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        fut.add_done_callback(lambda f: self.q.put(lambda: cb(f)))

    def ask(self, prompt, secret=False):
        box = queue.Queue()
        self.q.put(lambda: box.put(simpledialog.askstring("Вхід у Telegram", prompt, parent=self.root,
                                                          show="*" if secret else None)))
        return box.get() or ""

    def set_status(self, text):
        self.status_text = text
        if text.startswith("Канал"):
            self.status_state = "ok"
        elif text.startswith(("Помилка", "Потрібні")):
            self.status_state = "err"
        elif text.startswith("Підключення"):
            self.status_state = "busy"
        try:
            self.banner.set_status(short_status(text), text, STATE_COLOR.get(self.status_state, "#f2994a"))
        except (tk.TclError, AttributeError):
            pass

    def ping(self, note):
        """Показує в статусі, що з каналу щось прийшло (допомагає перевірити, чи працюють події)."""
        if self.channel_title:
            self.set_status(f"Канал: {self.channel_title} · {note} {datetime.now(TZ):%H:%M:%S}")

    # --- підключення ---
    def start(self):
        if TelegramClient is None:
            messagebox.showerror("Оплати", "Не встановлено бібліотеку telethon.\nВиконайте: pip install telethon")
            return
        if not all(self.cfg.get(k) for k in ("API_ID", "API_HASH", "CHANNEL_NAME")):
            if not self.open_settings(first=True):
                self.set_status("Потрібні налаштування")
                return
        self.set_status("Підключення…")
        self.run(self._connect(), self.on_connected)

    async def _connect(self):
        self.client = TelegramClient(os.path.join(BASE, "pay_session"), int(self.cfg["API_ID"]), self.cfg["API_HASH"])
        await self.client.start(phone=lambda: self.ask("Номер телефону у форматі +380…"),
                                code_callback=lambda: self.ask("Код із Telegram"),
                                password=lambda: self.ask("Пароль двоетапної перевірки", secret=True))
        name = self.cfg["CHANNEL_NAME"].lower()
        names = []
        async for d in self.client.iter_dialogs():
            names.append(d.name)
            if name in d.name.lower():
                self.entity = d.entity
                self.client.add_event_handler(self._on_new, events.NewMessage(chats=self.entity))
                self.client.add_event_handler(self._on_edit, events.MessageEdited(chats=self.entity))
                return d.name
        raise RuntimeError("Канал не знайдено. Ваші чати:\n- " + "\n- ".join(names[:30]) +
                           "\n\nЗмініть назву в «Налаштування» і перезапустіть програму.")

    def on_connected(self, fut):
        try:
            title = fut.result()
        except Exception as e:
            self.set_status("Помилка підключення")
            messagebox.showerror("Оплати", str(e))
            return
        self.ready = True
        self.channel_title = title
        self.set_status(f"Канал: {title}")
        self.fetch(self.date_var.get())
        self.refresh_plan(True)

    # --- нові оплати: сповіщення ---
    async def _on_new(self, event):
        await self._ingest(event, False)

    async def _on_edit(self, event):
        await self._ingest(event, True)

    async def _ingest(self, event, edited):
        try:
            pays = parse(event.raw_text)
            mid = getattr(event, "id", None)
            if edited and (not pays or mid in self.seen_ids):
                return  # редагування вже враховаєного повідомлення ігноруємо
            if not pays:
                self.q.put(lambda: self.ping("повідомлення без оплат"))
                return
            if mid is not None:
                self.seen_ids.add(mid)
                if len(self.seen_ids) > 5000:
                    self.seen_ids.clear()
            mdt = event.date.astimezone(TZ)
            for p in pays:
                p["dt"] = pay_dt(mdt, p.get("hm"))
            self.q.put(lambda: self.handle_new(pays))
            self.q.put(lambda: self.ping("оплата, " + getattr(self, "notify_note", "")))
        except Exception:
            log_error("_ingest")

    def handle_new(self, pays):
        enabled = self.notify_cfg()
        only_group = self.cfg.get("NOTIFY_ONLY_GROUP", False)
        fired = False
        shown, skipped = 0, []
        for p in pays:
            t = classify(p["kind"])
            if not enabled.get(t, True):
                skipped.append(f"тип {TYPE_LABEL[t]} вимкнено")
                continue
            if only_group and self.group != "all" and find_group(p["model"]) != self.group:
                skipped.append("інша група")
                continue
            shown += 1
            body = f"{display_name(p['model'])}  ·  {fmt(p['net'])}"
            self.popup("Нова оплата · " + TYPE_LABEL[t] if t not in ("new_sub", "renew") else TYPE_LABEL[t],
                       body, TYPE_COLOR[t])
            fired = True
        self.notify_note = f"сповіщень: {shown}" + (f" ({', '.join(skipped)})" if skipped else "")
        log_note(f"оплат {len(pays)}; {self.notify_note}; вікно={self.root.state()}")
        if fired and self.cfg.get("NOTIFY_SOUND", True):
            self.beep()
        if self.range:
            mine = [p for p in pays if self.range[0] <= p["dt"] <= self.range[1]]
        else:
            mine = [p for p in pays if p["dt"].date().isoformat() == self.day]
        self.payments.extend(mine)
        key = self.mcache.get("key")
        if key:
            self.mcache["pays"].extend(p for p in pays if p["dt"].strftime("%Y-%m") == key)
        self.render()
        if any(self.group == "all" or find_group(p["model"]) == self.group for p in pays):
            self.pay_wave()

    def pay_wave(self):
        """Хвиля в шапці та пульс кільця при новій оплаті."""
        try:
            self.banner.ripple()
            self.pulse_ring()
        except (tk.TclError, AttributeError):
            pass

    def beep(self):
        play_pay_sound(self.root)

    def popup(self, title, body, color, pos=None):
        try:
            self._popup(title, body, color, pos)
        except Exception:
            log_error("popup")

    def _layout_popups(self):
        used = {}
        for w in list(self.popups):
            try:
                w.update_idletasks()
                width, height = w.winfo_reqwidth(), w.winfo_reqheight()
                pos = w._pos
                acc = used.get(pos, 0)
                margin = 16
                x = margin if pos[1] == "l" else w.winfo_screenwidth() - width - margin
                y = margin + acc if pos[0] == "t" else w.winfo_screenheight() - height - 60 - acc
                w.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")
                used[pos] = acc + height + 8
            except tk.TclError:
                pass

    def _popup(self, title, body, color, pos=None):
        pos = pos or self.cfg.get("NOTIFY_POS", "br")
        if pos not in ("tl", "tr", "bl", "br"):
            pos = "br"
        w = tk.Toplevel(self.root)
        style_titlebar(w)
        w.withdraw()
        w._pos = pos
        w.overrideredirect(True)
        w.configure(bg=color)
        inner = tk.Frame(w, bg=P["surf"])
        inner.pack(padx=1, pady=1)
        tk.Frame(inner, bg=color, width=6).pack(side="left", fill="y")
        box = tk.Frame(inner, bg=P["surf"])
        box.pack(side="left", padx=(12, 12), pady=(8, 0))
        head = tk.Frame(box, bg=P["surf"])
        head.pack(fill="x")
        if xmas():
            hat = tk.Canvas(head, width=36, height=24, bg=P["surf"], highlightthickness=0, bd=0)
            hat.pack(side="left", padx=(0, 6))
            draw_hat(hat, 7, 15, 24)
        tk.Label(head, text=title, bg=P["surf"], fg=color, font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Label(head, text="✕", bg=P["surf"], fg=P["muted"], font=("Segoe UI", 9), cursor="hand2").pack(side="right", padx=(18, 0))
        tk.Label(box, text=body, bg=P["surf"], fg=P["ink"], font=("Segoe UI", 12, "bold"), wraplength=300,
                 justify="left").pack(anchor="w", pady=(2, 6))
        if xmas():       # рядок сніжинок під сумою
            flakes = tk.Canvas(box, height=14, width=260, bg=P["surf"], highlightthickness=0, bd=0)
            flakes.pack(fill="x")
            ice = "#cfe8ff" if P.get("theme") == "dark" else "#6aaee0"
            for i, (y, size) in enumerate(((8, 10), (5, 7), (9, 8), (6, 11), (8, 7), (5, 9), (9, 10), (6, 7))):
                flakes.create_text(14 + i * 33, y, text="❄", fill=ice, font=("Segoe UI Symbol", size))
        timer = tk.Canvas(box, height=3, width=260, bg=P["surf"], highlightthickness=0, bd=0)
        timer.pack(fill="x", pady=(0, 6))
        self.popups.append(w)
        self._layout_popups()
        try:
            w.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        w.deiconify()
        w.attributes("-topmost", True)
        w.lift()
        state = {"closed": False}

        def close(_e=None):
            if state["closed"]:
                return
            state["closed"] = True
            if w in self.popups:
                self.popups.remove(w)
            try:
                w.destroy()
            except Exception:
                pass
            self._layout_popups()

        def fade(a=0.0):
            if state["closed"]:
                return
            try:
                w.attributes("-alpha", min(a, 0.97))
            except tk.TclError:
                return
            if a < 0.97:
                w.after(20, fade, a + 0.16)

        total = 140  # 140 кадрів по 50 мс = 7 секунд

        def tick(i=0):
            if state["closed"]:
                return
            timer.delete("all")
            timer.create_rectangle(0, 0, timer.winfo_width() * (1 - i / total), 3, fill=color, width=0)
            if i >= total:
                close()
            else:
                w.after(50, tick, i + 1)

        def bind_all(widget):
            widget.bind("<Button-1>", close)
            for c in widget.winfo_children():
                bind_all(c)
        bind_all(w)
        fade()
        tick()

        def check():
            try:
                log_note(f"вікно '{title}': viewable={bool(w.winfo_viewable())} geom={w.geometry()} "
                         f"екран={w.winfo_screenwidth()}x{w.winfo_screenheight()} root={self.root.state()}")
            except Exception:
                pass
        w.after(250, check)

    # --- завантаження і показ ---
    def pick(self, days_ago):
        self.date_var.set((datetime.now(TZ).date() - timedelta(days=days_ago)).isoformat())
        self.fetch(self.date_var.get())

    def fetch(self, day_str):
        if not self.ready:
            messagebox.showinfo("Оплати", "Ще триває підключення до Telegram.")
            return
        day = parse_date(day_str)
        if day is None:
            messagebox.showinfo("Оплати", "Дата має бути у форматі 2026-11-24")
            return
        self.mcache, self.mfail = {}, None
        self.range = None
        self.set_status("Завантажую…")
        self.run(self._fetch_range(day, day), lambda f, d=day: self.on_fetched(f, d))

    def set_icon(self):
        try:
            from PIL import ImageTk
            img = make_icon(P["acc"], hat=xmas())
            if img is not None:
                self._icon = ImageTk.PhotoImage(img)
                self.root.iconphoto(True, self._icon)
        except Exception:
            pass

    def paint_period_panel(self, after=None):
        open_ = bool(self.cfg.get("PERIOD_OPEN", False))
        if open_:
            self.pframe.pack(fill="x", pady=(0, 2), after=after) if after is not None else self.pframe.pack(fill="x", pady=(0, 2))
        else:
            self.pframe.pack_forget()
        paint_toggle(self.pbtn, open_)
        self.pbtn.configure(text="Період ▴" if open_ else "Період ▾")

    def toggle_period(self):
        self.cfg["PERIOD_OPEN"] = not bool(self.cfg.get("PERIOD_OPEN", False))
        self._save()
        self.paint_period_panel(self.pbtn.master)

    def on_tree_motion(self, e):
        iid = self.tree.identify_row(e.y)
        if iid != self._hover_iid:
            self.set_hover(iid)

    def set_hover(self, iid):
        """Підсвічує рядок анкети під курсором; початкові теги (зебра) повертаються, коли курсор пішов."""
        if self._hover_iid is not None and self.tree.exists(self._hover_iid):
            self.tree.item(self._hover_iid, tags=self._hover_tags)
        self._hover_iid, self._hover_tags = None, ()
        if iid and iid in self.row_models and self.tree.exists(iid):
            self._hover_iid, self._hover_tags = iid, tuple(self.tree.item(iid, "tags"))
            self.tree.item(iid, tags=("hover",))

    def on_row(self, _e=None):
        sel = self.tree.selection()
        model = self.row_models.get(sel[0]) if sel else None
        if model:
            self.show_detail(model)

    def detail_rows(self, model):
        """Окремі оплати анкети за ті самі фільтри, що й у таблиці (зміни, підписки), найновіші зверху."""
        shifts = self.eff_shifts()
        rows = []
        for p in bigwish_full(self.payments):
            if p["model"] != model:
                continue
            if p.get("dt") is not None and shift_of(p["dt"]) not in shifts:
                continue
            if self.nosub.get() and "підписк" in p["kind"].lower():
                continue
            rows.append(p)
        rows.sort(key=lambda p: p["dt"] if p.get("dt") is not None else datetime.min.replace(tzinfo=TZ), reverse=True)
        return rows

    def show_detail(self, model):
        rows = self.detail_rows(model)
        win = tk.Toplevel(self.root)
        style_titlebar(win)
        win.title(f"{display_name(model)} · оплати")
        win.configure(bg=P["bg"])
        win.geometry("500x480")
        win.minsize(420, 320)
        win.transient(self.root)
        head = tk.Frame(win, bg=P["bg"])
        head.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(head, text=display_name(model), bg=P["bg"], fg=P["ink"], font=("Segoe UI Semibold", 14)).pack(side="left")
        tk.Label(head, text=self.period_label(), bg=P["bg"], fg=P["muted"], font=FONT).pack(side="right")
        total = sum(p["net"] for p in rows)
        bw = sum(p["net"] for p in rows if p.get("bigwish"))
        para = sum(p["net"] for p in rows if not p.get("bigwish") and p.get("para"))
        info = f"{len(rows)} пл. · {fmt(total)}" + (f" · BigWish {fmt(bw)}" if bw else "") + (f" · Para {fmt(para)}" if para else "")
        tk.Label(win, text=info, bg=P["bg"], fg=P["acc_text"], font=FONT_B).pack(anchor="w", padx=14, pady=(0, 6))

        bot = tk.Frame(win, bg=P["bg"])
        bot.pack(side="bottom", fill="x", padx=14, pady=10)
        tf = tk.Frame(win, bg=P["surf"], highlightthickness=1, highlightbackground=P["line"])
        tf.pack(fill="both", expand=True, padx=14)
        tree = ttk.Treeview(tf, columns=("time", "kind", "src", "sum"), show="headings", selectmode="browse")
        for col, text, w, anc in (("time", "Час", 100, "w"), ("kind", "Тип", 170, "w"),
                                  ("src", "Джерело", 80, "w"), ("sum", "Сума", 80, "e")):
            tree.heading(col, text=text, anchor=anc)
            tree.column(col, width=w, minwidth=50, anchor=anc, stretch=(col == "kind"))
        tree.tag_configure("odd", background=mix(P["surf"], P["bg"], 0.55))
        sb = ttk.Scrollbar(tf, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        lines = []
        for i, p in enumerate(rows):
            when = p["dt"].strftime("%d.%m %H:%M") if p.get("dt") is not None else "—"
            src = "BigWish" if p.get("bigwish") else ("Para" if p.get("para") else "")
            kind = TYPE_LABEL[classify(p["kind"])]
            tree.insert("", "end", values=(when, kind, src, fmt(p["net"])), tags=(("odd",) if i % 2 else ()))
            lines.append(f"{when}\t{kind}\t{src}\t{p['net']:.2f}")
        if not rows:
            tk.Label(tf, text="Оплат за цей вибір немає", bg=P["surf"], fg=P["muted"], font=FONT).place(relx=.5, rely=.5, anchor="center")

        def copy_it():
            self.root.clipboard_clear()
            self.root.clipboard_append(f"{display_name(model)}, {self.period_label()}\n" + "\n".join(lines))
            self.set_status("Скопійовано")
        make_btn(bot, "Копіювати список", copy_it, True).pack(side="left")
        make_btn(bot, "Закрити", win.destroy).pack(side="right")
        win.bind("<Escape>", lambda _e: win.destroy())

    def period_label(self):
        if self.range:
            return f"{self.range[0]:%d.%m %H:%M} – {self.range[1]:%d.%m %H:%M}"
        return self.day

    def eff_shifts(self):
        return set(ALL_SHIFTS) if self.range else self.shifts

    def fetch_period(self):
        if not self.ready:
            messagebox.showinfo("Оплати", "Ще триває підключення до Telegram.")
            return
        a, b = parse_dt(self.r_from.get()), parse_dt(self.r_to.get())
        if a is None or b is None:
            messagebox.showinfo("Оплати", "Вкажіть початок і кінець: дата та година, напр. 14.11 08:00 і 16.11 22:00")
            return
        if b < a:
            messagebox.showinfo("Оплати", "Кінець періоду раніше за початок.")
            return
        self.set_status("Завантажую…")
        self.run(self._fetch_range(a.date(), b.date()), lambda f: self.on_period(f, a, b))

    def on_period(self, fut, a, b):
        try:
            pays = fut.result()
        except Exception as e:
            self.set_status("Помилка завантаження")
            messagebox.showerror("Оплати", str(e))
            return
        self.payments = [p for p in pays if a <= p["dt"] <= b]
        self.range = (a, b)
        self.day = b.date().isoformat()
        self.set_status(f"Оновлено {datetime.now(TZ):%H:%M}")
        self.render()

    async def _fetch_range(self, d1, d2):
        """Усі оплати з d1 по d2 включно (за реальним часом оплати з рядка 🕒)."""
        start = datetime.combine(d1, dtime.min, TZ)
        end = datetime.combine(d2, dtime.min, TZ) + timedelta(days=1, minutes=30)  # запас: 23:5x може прийти після півночі
        out = []
        async for m in self.client.iter_messages(self.entity, offset_date=end.astimezone(timezone.utc)):
            if m.date < start:
                break
            if not m.raw_text:
                continue
            mdt = m.date.astimezone(TZ)
            for p in parse(m.raw_text):
                p["dt"] = pay_dt(mdt, p.get("hm"))
                if d1 <= p["dt"].date() <= d2:
                    out.append(p)
        return out

    def on_fetched(self, fut, day):
        try:
            pays = fut.result()
        except Exception as e:
            self.set_status("Помилка завантаження")
            messagebox.showerror("Оплати", str(e))
            return
        self.payments = pays
        self.day = day.isoformat()
        self.set_status(f"Оновлено {datetime.now(TZ):%H:%M}")
        self.render()

    def set_group(self, g):
        self.group = g
        self.cfg["GROUP"] = g
        try:
            save_cfg(self.cfg)
        except Exception:
            pass
        self.paint_groups()
        self.render()

    def render(self):
        r = aggregate(bigwish_full(self.payments), self.group, self.nosub.get(), self.eff_shifts())
        self.v_cnt.set(str(r["tc"]))
        self.animate_net(r["tn"])
        self.tree.delete(*self.tree.get_children())
        self.row_models = {}
        self._hover_iid, self._hover_tags = None, ()
        n = 0
        for g in sorted(r["data"]):
            if self.group == "all":
                self.tree.insert("", "end", values=("Без групи" if g == "—" else f"Група {g}", "", "", "", ""), tags=("grp",))
                n = 0
            for model, (c, ne, bw, para) in sorted(r["data"][g].items(), key=lambda kv: -kv[1][1]):
                iid = f"m{len(self.row_models)}"
                self.row_models[iid] = model
                self.tree.insert("", "end", iid=iid, values=(display_name(model), c, fmt(ne), fmt(bw) if bw else "",
                                                             fmt(para) if para else ""), tags=(("odd",) if n % 2 else ()))
                n += 1
        notes = []
        if r["subs"]:
            notes.append(f"підписок виключено: {r['subs']} ({fmt(r['subs_sum'])})")
        if r["other"]:
            notes.append(f"інші групи: {r['other']}")
        if not self.payments and self.day:
            notes.append("За цей день оплат не знайдено.")
        self.notes.configure(text=" · ".join(notes))
        self.last_text = report_text(r, self.group, self.period_label(), self.nosub.get(), self.eff_shifts())
        best, shifts = None, self.eff_shifts()
        for p in bigwish_full(self.payments):
            if p.get("dt") is None or shift_of(p["dt"]) not in shifts:
                continue
            if self.group != "all" and find_group(p["model"]) != self.group:
                continue
            if self.nosub.get() and "підписк" in p["kind"].lower():
                continue
            if best is None or p["dt"] >= best["dt"]:
                best = p
        self._last_pay = best
        self.refresh_last()
        self.update_plan()

    COPY_MODELS = [("MOT", ["master on twinks", "mot"]), ("Marie", ["marie sauvage"]),
                   ("Ben Ryder", ["ben ryder"]), ("Benny", ["benny"])]

    def copy_text(self):
        """Звіт для копіювання: суми по анкетах за обраний період (BigWish без комісії) + окремо BW і Para, якщо були."""
        names = [n for n, _ in self.COPY_MODELS]
        sums = {n: 0.0 for n in names}
        bw = {n: 0.0 for n in names}
        para = {n: 0.0 for n in names}
        shifts = self.eff_shifts()
        for p in bigwish_full(self.payments):
            if p.get("dt") is not None and shift_of(p["dt"]) not in shifts:
                continue
            if self.nosub.get() and "підписк" in p["kind"].lower():
                continue
            n = norm(p["model"])
            for name, aliases in self.COPY_MODELS:
                if any((n == a) if a == "mot" else (a in n) for a in aliases):
                    sums[name] += p["net"]
                    if p.get("bigwish"):
                        bw[name] += p["gross"]
                    elif p.get("para"):
                        para[name] += p["gross"]
                    break
        lines = []
        for name in names:
            line = f"+{sums[name]:.2f}$"
            if bw[name] > 0:
                line += f" - bw {bw[name]:.2f}$"
            lines += [name, line]
            if para[name] > 0:
                lines.append(f"Para - {para[name]:.2f}$")
            lines.append("")
        try:
            plan = float((self.cfg.get("AUTO_PLAN") or {}).get("shift") or 0)
        except (TypeError, ValueError):
            plan = 0.0
        lines.append(f"Plan: ${plan:.2f}" if plan > 0 else "Plan: —")
        lines.append(f"Total: ${sum(sums.values()):.2f}")
        return "\n".join(lines)

    def copy(self):
        if not self.day:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.copy_text())
        self.set_status("Скопійовано")

    # --- ставка, курс, початок обліку ---
    # --- план і прогрес ---
    def plan_mode(self):
        m = self.cfg.get("PLAN_MODE")
        return m if m in PLAN_MODES else "day"

    def plan_auto(self, mode):
        """План з каналу (з фото) для режиму; діє лише для групи з цими анкетами."""
        ap = self.cfg.get("AUTO_PLAN") or {}
        try:
            v = float(ap.get(mode) or 0)
        except (TypeError, ValueError):
            v = 0.0
        return v if v > 0 and self.group == find_group("benny") else None

    def month_from_photo(self):
        """Місяць = «Total REVENUE» з фото за вчора + оплати сьогодні (без завантаження всього місяця)."""
        if self.plan_auto("month") is None:
            return None
        ap = self.cfg.get("AUTO_PLAN") or {}
        bdate, day = parse_date(ap.get("month_date")), parse_date(self.day)
        try:
            done = float(ap.get("month_done") or 0)
        except (TypeError, ValueError):
            return None
        if not (done > 0 and bdate and day and bdate.month == day.month and bdate.year == day.year):
            return None
        if bdate == day:
            return done
        if bdate == day - timedelta(days=1):
            return done + aggregate(self.payments, self.group, False, ALL_SHIFTS)["tn"]
        return None

    def plan_goal(self):
        return self.plan_auto(self.plan_mode()) or 0.0

    def paint_plan(self):
        for k, b in self.pbtns.items():
            paint_toggle(b, k == self.plan_mode())

    def set_plan_mode(self, mode):
        self.cfg["PLAN_MODE"] = mode
        self._save()
        self.paint_plan()
        self.update_plan()

    def plan_earned(self, mode):
        """(заробіток, повідомлення). План «День» і «Місяць» завжди враховує підписки."""
        extra = 0.0
        if mode == "month":
            fast = self.month_from_photo()
            if fast is not None:
                return fast, ""
            key = self.day[:7]
            if self.mcache.get("key") != key:
                if self.mfail == key:
                    return None, "Не вдалося завантажити місяць. Натисніть «Порахувати»."
                self.load_month(key)
                return None, "Завантажую місяць…"
            pays = self.mcache["pays"]
            y, m = int(key[:4]), int(key[5:7])
            extra = manual_total(self.group, date(y, m, 1), date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)) or 0.0
        else:
            # План «День»: чисті оплати, але BigWish рахуємо без комісії (повна сума)
            pays = bigwish_full(self.payments)
        r = aggregate(pays, self.group, False, ALL_SHIFTS)
        val = r["tn"] + extra
        return val, ""

    # --- план з каналу (фото) ---
    def refresh_plan(self, silent=True):
        if not self.ready:
            if not silent:
                messagebox.showinfo("Оплати", "Ще триває підключення до Telegram.")
            return
        self.set_status("Оновлюю план…")
        self.run(self._fetch_plan(), lambda f: self.on_plan(f, silent))

    def calc_chatter(self):
        if not self.ready:
            messagebox.showinfo("Оплати", "Ще триває підключення до Telegram.")
            return
        self.set_status("Читаю таблицю з фото…")
        self.run(self._fetch_plan(), lambda f: self.on_plan(f, False, True))

    async def _fetch_plan(self):
        if not OCR_OK:
            if getattr(sys, "frozen", False):
                raise RuntimeError("У цій збірці .exe немає pytesseract/pillow.\n\n"
                                   "1) Встановіть: pip install pytesseract pillow\n"
                                   "2) Перезберіть .exe (build_app.bat)\n"
                                   "3) Запускайте новий .exe з папки dist.")
            raise RuntimeError("Не вдалося підключити pytesseract/pillow.\n\n"
                               f"Причина: {OCR_ERR}\n\n"
                               f"Програма запущена цим Python:\n{sys.executable}\n\n"
                               "Встановіть саме в нього командою:\n"
                               f'"{sys.executable}" -m pip install pytesseract pillow\n\n'
                               "Потім перезапустіть програму.")
        name = (self.cfg.get("PLAN_CHANNEL") or "TiER2").lower()
        ent = None
        async for d in self.client.iter_dialogs():
            if name in d.name.lower():
                ent = d.entity
                break
        if ent is None:
            raise RuntimeError(f"Канал «{name}» не знайдено. Перевірте назву в «Налаштування».")
        cmd = self.cfg.get("TESSERACT_PATH") or None
        topic = (self.cfg.get("PLAN_TOPIC") or "general").strip().lower()
        attempts = [{"reply_to": 1}, {}] if topic in ("", "general") else [{}]
        found = {}
        for i, kw in enumerate(attempts):
            seen = 0
            try:
                async for m in self.client.iter_messages(ent, limit=400, **kw):
                    if not is_image_msg(m):
                        continue
                    seen += 1
                    if seen > 40:
                        break
                    data = await m.download_media(bytes)
                    res = await asyncio.to_thread(read_plan_image, data, cmd)
                    for k, v in (res or {}).items():
                        found.setdefault(k, v)
                    if "day" in found and "month" in found:
                        break
            except Exception:
                if i == len(attempts) - 1:
                    raise
                continue
            if found:
                break
        if not found:
            raise RuntimeError("Не вдалося знайти і розпізнати фото з планом у каналі.")
        return found

    def on_plan(self, fut, silent, chatter=False):
        try:
            found = fut.result()
        except Exception as e:
            self.set_status("План: помилка")
            if not silent:
                messagebox.showerror("План з каналу", str(e))
            return
        ap = dict(self.cfg.get("AUTO_PLAN") or {})
        ap.update(found)
        self.cfg["AUTO_PLAN"] = ap
        self._save()
        got = ", ".join(k for k in ("day", "month") if k in found)
        self.set_status(f"План оновлено {datetime.now(TZ):%H:%M} ({got})")
        self.update_plan()
        if chatter:
            self.show_chatter()

    def show_chatter(self):
        ap = self.cfg.get("AUTO_PLAN") or {}
        models = ap.get("models") or {}
        if not models:
            messagebox.showinfo("Заробіток чатера", "На фото не вдалося розпізнати таблицю по анкетах.")
            return
        win = tk.Toplevel(self.root)
        style_titlebar(win)
        win.title("Заробіток чатера")
        win.configure(bg=P["bg"])
        win.geometry("560x330")
        win.minsize(500, 280)
        when = parse_date(ap.get("month_date"))
        tk.Label(win, text="Заробіток чатера", bg=P["bg"], fg=P["ink"], font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
        tk.Label(win, text=(f"З фото за {when:%d.%m.%Y}: місяць до цієї дати, оплати за сьогодні не враховано · BigWish і Para −{CHATTER_FEE * 100:.0f}%"
                            if when else "З фото: місяць до дати на фото · BigWish і Para −17%"),
                 bg=P["bg"], fg=P["muted"], font=("Segoe UI", 8), anchor="w").pack(fill="x", padx=12)
        rows, lines, notes = [], [], []
        tot = {"rev": 0.0, "bw": 0.0, "para": 0.0, "earn": 0.0}
        for name, pct in CHATTER_PCT.items():
            m = models.get(name)
            if not m:
                notes.append(f"{name}: не розпізнано")
                continue
            rev, bw, para = float(m.get("rev", 0)), float(m.get("bw", 0)), float(m.get("para", 0))
            k = 1 - CHATTER_FEE
            earn = rev * pct["rev"] / 100 + bw * k * pct["bw"] / 100 + para * k * pct.get("para", 0) / 100
            if para and "para" not in pct:
                notes.append(f"{name}: немає % для Para — не враховано")
            for k, v in (("rev", rev), ("bw", bw), ("para", para), ("earn", earn)):
                tot[k] += v
            rows.append((name, fmt0(rev), fmt0(bw), fmt0(para) if para else "—", fmt(earn)))
            lines.append(f"{name}: {fmt(earn)}")
        rows.append(("Разом", fmt0(tot["rev"]), fmt0(tot["bw"]), fmt0(tot["para"]) if tot["para"] else "—", fmt(tot["earn"])))
        lines.append(f"РАЗОМ: {fmt(tot['earn'])}")
        text = (f"Заробіток чатера" + (f" (до {when:%d.%m.%Y})" if when else "") + "\n" + "\n".join(lines))

        bot = tk.Frame(win, bg=P["bg"])
        bot.pack(side="bottom", fill="x", padx=12, pady=8)

        def copy_it():
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.set_status("Скопійовано")
        make_btn(bot, "Копіювати", copy_it, True).pack(side="left")
        if notes:
            tk.Label(bot, text="   ".join(notes), bg=P["bg"], fg=P["muted"], font=("Segoe UI", 8)).pack(side="left", padx=8)
        tf = tk.Frame(win, bg=P["surf"], highlightthickness=1, highlightbackground=P["line"])
        tf.pack(fill="both", expand=True, padx=12, pady=(8, 0))
        cols = ("n", "rev", "bw", "para", "earn")
        tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="browse")
        for col, head, w, anc in (("n", "Анкета", 110, "w"), ("rev", "Звичайні", 90, "e"), ("bw", "BigWish", 90, "e"),
                                  ("para", "Para", 70, "e"), ("earn", "Чатеру", 100, "e")):
            tree.heading(col, text=head, anchor=anc)
            tree.column(col, width=w, minwidth=w // 2, anchor=anc, stretch=(col == "n"))
        tree.tag_configure("total", background=P["bg"], font=("Segoe UI", 9, "bold"))
        for i, r in enumerate(rows):
            tree.insert("", "end", values=r, tags=("total",) if i == len(rows) - 1 else ())
        tree.pack(fill="both", expand=True)

    def load_month(self, key):
        if not self.ready or self.mloading == key:
            return
        y, m = int(key[:4]), int(key[5:7])
        d1 = date(y, m, 1)
        d2 = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
        self.mloading = key
        self.run(self._fetch_range(d1, d2), lambda f: self.on_month(f, key))

    def on_month(self, fut, key):
        self.mloading = None
        try:
            pays = fut.result()
        except Exception:
            self.mfail = key
        else:
            self.mfail = None
            self.mcache = {"key": key, "pays": pays}
        self.update_plan()

    def update_plan(self):
        mode, goal = self.plan_mode(), self.plan_goal()
        self.plan_frac = 0.0
        if not self.day:
            text = "Дані ще не завантажено"
        elif self.range:
            text = f"Період {self.period_label()}\nПлан для довільного періоду не рахується"
        else:
            earned, note = self.plan_earned(mode)
            ctx = self.day if mode == "day" else self.day[:7]
            if earned is None:
                text = note
            elif goal <= 0:
                text = f"{PLAN_MODES[mode]}: {fmt(earned)} · {ctx}\nПлану з каналу немає, натисніть «Оновити»"
            else:
                self.plan_frac = earned / goal
                key = (mode, ctx)
                prev = self._frac_seen.get(key)
                if prev is not None and prev < 1 <= self.plan_frac:
                    self.banner.confetti()
                self._frac_seen[key] = self.plan_frac
                left = goal - earned
                tail = f"Лишилось {fmt(left)}" if left > 0 else f"План виконано, +{fmt(-left)} понад"
                text = f"{PLAN_MODES[mode]}: {fmt(earned)} із {fmt(goal)} · {self.plan_frac * 100:.0f}%\n{tail} · {ctx}"
        self.plan_lbl.configure(text=text)
        self.set_bar(self.plan_frac)

    def draw_bar(self):
        """Кільце плану: коло прогресу навколо кількості платежів."""
        c = self.ring
        try:
            c.delete("ring")
            mid = RING / 2
            box = (mid - RING_R, mid - RING_R, mid + RING_R, mid + RING_R)
            c.create_oval(*box, outline=P["line"], width=RING_W, tags="ring")
            f = min(max(self._bar_val, 0.0), 1.0)
            if f > 0.002:
                c.create_arc(*box, start=90, extent=-min(f, 0.9999) * 360, style="arc",
                             outline=plan_color(f), width=RING_W, tags="ring")
            c.create_text(mid, mid - 6, text=self.v_cnt.get(), fill=P["ink"], font=("Segoe UI Semibold", 18), tags="ring")
            c.create_text(mid, mid + 14, text="платежів", fill=P["muted"], font=("Segoe UI", 8), tags="ring")
        except tk.TclError:
            pass

    def pulse_ring(self):
        """Дві хвилі, що розходяться від кільця."""
        c, mid = self.ring, RING / 2

        def step(i):
            try:
                c.delete("wave")
                if i > 26:
                    return
                for k in (0, 7):
                    j = i - k
                    if 0 <= j <= 18:
                        t = j / 18
                        r = RING_R + 5 + t * (mid - RING_R - 6)
                        c.create_oval(mid - r, mid - r, mid + r, mid + r, outline=mix(P["acc"], P["surf"], t ** 0.6),
                                      width=2, tags="wave")
                c.after(30, step, i + 1)
            except tk.TclError:
                pass
        step(0)

    def refresh_last(self):
        """Рядок під сумою: «Benny +$100 · 2 хв тому»."""
        lp = self._last_pay
        try:
            if lp is None:
                self.last_a.configure(text="")
                self.last_b.configure(text="Оплат за цей вибір немає" if self.day else "")
            else:
                self.last_a.configure(text=f"{display_name(lp['model'])} +{money_short(lp['net'])}")
                self.last_b.configure(text=" · " + ago(lp.get("dt")))
        except (tk.TclError, AttributeError):
            pass

    def _last_tick(self):
        """Раз на 15 с оновлює «хв тому» без жодних дій користувача."""
        self.refresh_last()
        try:
            self.root.after(15000, self._last_tick)
        except tk.TclError:
            pass

    def set_bar(self, target):
        """Плавно доводить смугу плану до нового значення."""
        if self._bar_job is not None:
            try:
                self.root.after_cancel(self._bar_job)
            except Exception:
                pass
            self._bar_job = None
        start, steps = self._bar_val, 20

        def step(i):
            t = i / steps
            self._bar_val = start + (target - start) * (1 - (1 - t) ** 3)
            if i >= steps:
                self._bar_val = target
            try:
                self.draw_bar()
            except tk.TclError:
                return
            if i < steps:
                self._bar_job = self.root.after(20, step, i + 1)
            else:
                self._bar_job = None
        if abs(target - start) < 0.001:
            self._bar_val = target
            self.draw_bar()
        else:
            step(1)

    def animate_net(self, target):
        """Сума «докручується» до нового значення; при зростанні коротко світліша."""
        if self._net_job is not None:
            try:
                self.root.after_cancel(self._net_job)
            except Exception:
                pass
            self._net_job = None
        start, steps = self._net_val, 18
        if abs(target - start) < 0.005:
            self._net_val = target
            self.v_net.set(fmt(target))
            return
        grow = target > start

        def step(i):
            t = i / steps
            e = 1 - (1 - t) ** 3
            val = target if i >= steps else start + (target - start) * e
            self._net_val = val
            self.v_net.set(fmt(val))
            try:
                self.net_lbl.configure(fg=mix(P["acc_text"], P["ink"], 0.75 * (1 - e)) if grow else P["acc_text"])
            except tk.TclError:
                pass
            if i < steps:
                self._net_job = self.root.after(22, step, i + 1)
            else:
                self._net_job = None
        step(1)

    # --- редактор груп ---
    def open_groups(self):
        win = tk.Toplevel(self.root)
        style_titlebar(win)
        win.title("Групи")
        win.configure(bg=P["bg"], padx=14, pady=10)
        win.transient(self.root)
        win.grab_set()
        work = {g: list(al) for g, al in GROUPS.items()}
        cur = {"g": group_keys(work)[0]}

        def label(parent, text, **kw):
            kw.setdefault("fg", P["ink"])
            kw.setdefault("font", FONT)
            return tk.Label(parent, text=text, bg=P["bg"], **kw)

        def listbox(parent, **kw):
            return tk.Listbox(parent, bg=P["surf"], fg=P["ink"], font=FONT, relief="flat", bd=0, activestyle="none",
                              selectbackground=P["acc"], selectforeground=P["acc_ink"], highlightthickness=1,
                              highlightbackground=P["line"], highlightcolor=P["acc"], exportselection=False, **kw)

        label(win, "Імена анкет пишіть як у каналі: регістр, емодзі та «(OnlyFans)» не важливі, шукається збіг за частиною назви.",
              fg=P["muted"], font=("Segoe UI", 8), justify="left", wraplength=480).pack(anchor="w")
        body = tk.Frame(win, bg=P["bg"])
        body.pack(fill="both", expand=True, pady=(8, 0))

        left = tk.Frame(body, bg=P["bg"])
        left.pack(side="left", fill="y")
        label(left, "Групи", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        glist = listbox(left, width=16, height=10)
        glist.pack(pady=(2, 4))
        right = tk.Frame(body, bg=P["bg"])
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))
        title = label(right, "", font=("Segoe UI", 9, "bold"))
        title.pack(anchor="w")
        alist = listbox(right, width=32, height=10, selectmode="extended")
        alist.pack(fill="both", expand=True, pady=(2, 4))
        arow = tk.Frame(right, bg=P["bg"])
        arow.pack(fill="x")
        entry = make_entry(arow)
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        movebar = tk.Frame(win, bg=P["bg"])
        movebar.pack(fill="x", pady=(8, 0))
        info = label(win, "", fg=P["muted"], font=("Segoe UI", 8), anchor="w")
        info.pack(fill="x", pady=(4, 0))

        def keys():
            return group_keys(work)

        def fill_aliases():
            title.configure(text=f"Анкети групи {cur['g']}")
            alist.delete(0, "end")
            for a in work[cur["g"]]:
                alist.insert("end", a)

        def fill_move():
            for w in movebar.winfo_children():
                w.destroy()
            label(movebar, "Перенести вибрані в групу:").pack(side="left", padx=(0, 6))
            for g in keys():
                if g != cur["g"]:
                    make_btn(movebar, g, lambda g=g: move(g)).pack(side="left", padx=(0, 3))

        def fill_groups():
            glist.delete(0, "end")
            for g in keys():
                glist.insert("end", f"Група {g}  ({len(work[g])})")
            glist.selection_clear(0, "end")
            glist.selection_set(keys().index(cur["g"]))
            fill_aliases()
            fill_move()

        def on_group(_e=None):
            sel = glist.curselection()
            if sel and keys()[sel[0]] != cur["g"]:
                cur["g"] = keys()[sel[0]]
                fill_aliases()
                fill_move()

        def selected():
            return [work[cur["g"]][i] for i in alist.curselection()]

        def add(_e=None):
            name = norm(entry.get())
            if not name:
                return
            msg = ""
            for g, al in work.items():
                if name in al and g != cur["g"]:
                    al.remove(name)
                    msg = f"«{name}» перенесено з групи {g}"
                    break
            if name not in work[cur["g"]]:
                work[cur["g"]].append(name)
            info.configure(text=msg)
            entry.delete(0, "end")
            fill_groups()

        def remove():
            sel = set(selected())
            if sel:
                work[cur["g"]] = [a for a in work[cur["g"]] if a not in sel]
                info.configure(text="")
                fill_groups()

        def move(target):
            sel = selected()
            if not sel:
                info.configure(text="Спершу виберіть анкети у списку")
                return
            work[cur["g"]] = [a for a in work[cur["g"]] if a not in sel]
            work[target] += [a for a in sel if a not in work[target]]
            info.configure(text=f"Перенесено в групу {target}: {len(sel)}")
            fill_groups()

        def new_group():
            nums = [int(g) for g in work if g.isdigit()]
            g = str((max(nums) if nums else 0) + 1)
            work[g] = []
            cur["g"] = g
            info.configure(text=f"Створено групу {g}")
            fill_groups()
            entry.focus_set()

        def del_group():
            if work[cur["g"]]:
                info.configure(text="Спершу перенесіть анкети з цієї групи")
                return
            if len(work) == 1:
                return
            del work[cur["g"]]
            cur["g"] = keys()[0]
            info.configure(text="")
            fill_groups()

        def save():
            apply_groups(work)
            self.cfg["GROUPS"] = {g: list(al) for g, al in GROUPS.items()}
            if self.group != "all" and self.group not in GROUPS:
                self.group = group_keys()[0]
                self.cfg["GROUP"] = self.group
            self._save()
            win.destroy()
            self.rebuild()

        glist.bind("<<ListboxSelect>>", on_group)
        entry.bind("<Return>", add)
        make_btn(left, "+ Нова група", new_group).pack(fill="x", pady=(0, 3))
        make_btn(left, "Видалити порожню", del_group).pack(fill="x")
        make_btn(arow, "Додати", add, True).pack(side="left", padx=(4, 0))
        make_btn(arow, "Видалити вибрані", remove).pack(side="left", padx=(4, 0))
        brow = tk.Frame(win, bg=P["bg"])
        brow.pack(fill="x", pady=(10, 0))
        make_btn(brow, "Зберегти", save, True).pack(side="right")
        make_btn(brow, "Скасувати", win.destroy).pack(side="right", padx=(0, 6))
        fill_groups()

    # --- налаштування ---
    def open_settings(self, first=False):
        win = tk.Toplevel(self.root)
        style_titlebar(win)
        win.title("Налаштування")
        win.configure(bg=P["bg"], padx=16, pady=8)
        win.transient(self.root)
        win.grab_set()
        cols = tk.Frame(win, bg=P["bg"])
        cols.pack(fill="both", expand=True)
        L = tk.Frame(cols, bg=P["bg"])
        L.pack(side="left", anchor="n")
        R = tk.Frame(cols, bg=P["bg"])
        R.pack(side="left", anchor="n", padx=(26, 0))

        def head(parent, text):
            tk.Label(parent, text=text, bg=P["bg"], fg=P["ink"], font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(6, 2))

        def check(parent, text, var, **pk):
            return tk.Checkbutton(parent, text=text, variable=var, bg=P["bg"], fg=P["ink"], activebackground=P["bg"],
                                  activeforeground=P["ink"], selectcolor=P["surf"], font=FONT, highlightthickness=0)

        def radio(parent, text, val, var):
            return tk.Radiobutton(parent, text=text, value=val, variable=var, bg=P["bg"], fg=P["ink"],
                                  activebackground=P["bg"], activeforeground=P["ink"], selectcolor=P["surf"],
                                  font=FONT, highlightthickness=0)

        # Telegram
        head(L, "Telegram")
        tk.Label(L, text="api_id та api_hash: my.telegram.org → API development tools", bg=P["bg"],
                 fg=P["muted"], font=("Segoe UI", 8)).pack(anchor="w")
        entries = {}
        for k, label in (("API_ID", "api_id"), ("API_HASH", "api_hash"), ("CHANNEL_NAME", "Частина назви каналу")):
            tk.Label(L, text=label, bg=P["bg"], fg=P["ink"], font=FONT).pack(anchor="w", pady=(6, 0))
            e = make_entry(L, width=40)
            e.insert(0, str(self.cfg.get(k, "")))
            e.pack(anchor="w", ipady=5)
            entries[k] = e

        # Вигляд
        head(L, "Вигляд")
        chosen = {"accent": P["acc"]}
        theme_var = tk.StringVar(value=P.get("theme", "light"))
        trow = tk.Frame(L, bg=P["bg"])
        trow.pack(anchor="w")
        for val, label in (("light", "Світла"), ("dark", "Темна")):
            radio(trow, label, val, theme_var).pack(side="left", padx=(0, 14))
        tk.Label(L, text="Палітра", bg=P["bg"], fg=P["ink"], font=FONT).pack(anchor="w", pady=(8, 2))
        pal_var = {"n": self.cfg.get("PALETTE", "Класика") if self.cfg.get("PALETTE") in PALETTES else "Класика"}
        pal_btns = {}
        colors = {"bg": (self.cfg.get("COLORS") or {}).get("bg", ""),
                  "surf": (self.cfg.get("COLORS") or {}).get("surf", "")}
        color_ui = {}
        palrow = tk.Frame(L, bg=P["bg"])
        palrow.pack(anchor="w")

        def paint_pal():
            for n_, b_ in pal_btns.items():
                paint_toggle(b_, n_ == pal_var["n"])

        def pick_pal(n_):
            pal_var["n"] = n_
            colors["bg"] = colors["surf"] = ""
            for f_ in color_ui.values():
                f_()
            if n_ in PALETTES:
                chosen["accent"] = PALETTES[n_]["accent"]
                theme_var.set(PALETTES[n_]["theme"])
                paint()
            paint_pal()
        for i_, n_ in enumerate(["Класика"] + list(PALETTES)):
            b_ = make_btn(palrow, n_, lambda n_=n_: pick_pal(n_), height=26)
            b_.grid(row=i_ // 3, column=i_ % 3, sticky="w", padx=(0, 4), pady=(0, 4))
            pal_btns[n_] = b_
        paint_pal()
        anim_var = tk.BooleanVar(value=bool(self.cfg.get("ANIM", True)))
        tk.Checkbutton(L, text="Анімація шапки (сітка, хвилі, гірлянда — залежно від палітри)", variable=anim_var, bg=P["bg"],
                       activebackground=P["bg"], fg=P["ink"], activeforeground=P["ink"], selectcolor=P["surf"],
                       font=FONT, highlightthickness=0, bd=0).pack(anchor="w", pady=(8, 0))
        snow_var = tk.BooleanVar(value=bool(self.cfg.get("SNOW", True)))
        tk.Checkbutton(L, text="Частинки в шапці (сніг, бульбашки, іскри — залежно від палітри)", variable=snow_var, bg=P["bg"],
                       activebackground=P["bg"], fg=P["ink"], activeforeground=P["ink"], selectcolor=P["surf"],
                       font=FONT, highlightthickness=0, bd=0).pack(anchor="w")
        tk.Label(L, text="Колір акценту", bg=P["bg"], fg=P["ink"], font=FONT).pack(anchor="w", pady=(8, 2))
        srow = tk.Frame(L, bg=P["bg"])
        srow.pack(anchor="w")
        sw_buttons = {}
        cur = tk.Label(srow, text="  ", bg=chosen["accent"], width=4)

        def paint():
            cur.configure(bg=chosen["accent"])
            for c, b in sw_buttons.items():
                b.configure(highlightbackground=P["ink"] if c == chosen["accent"] else P["line"])

        def choose(c):
            chosen["accent"] = c
            paint()

        for c in SWATCHES:
            b = tk.Button(srow, bg=c, activebackground=c, width=2, relief="flat", bd=0, cursor="hand2",
                          highlightthickness=2, command=lambda c=c: choose(c))
            b.pack(side="left", padx=(0, 5))
            sw_buttons[c] = b

        def custom():
            res = colorchooser.askcolor(color=chosen["accent"], parent=win, title="Колір акценту")
            if res and res[1]:
                choose(res[1])
        make_btn(srow, "Інший…", custom).pack(side="left", padx=(6, 6))
        cur.pack(side="left")
        paint()

        # Кольори палітри: фон вікна і картки (з ними збігається фон кнопок)
        def base_color(key):
            pal = PALETTES.get(pal_var["n"])
            if pal:
                return pal[key]
            return (DARK if theme_var.get() == "dark" else LIGHT)[key]

        def color_row(key, title, swatches):
            tk.Label(L, text=title, bg=P["bg"], fg=P["ink"], font=FONT).pack(anchor="w", pady=(8, 2))
            r1 = tk.Frame(L, bg=P["bg"])
            r1.pack(anchor="w")
            r2 = tk.Frame(L, bg=P["bg"])
            r2.pack(anchor="w", pady=(4, 0))
            sw = {}
            prev = tk.Label(r2, text="  ", width=4, bg=colors[key] or base_color(key))

            def repaint():
                prev.configure(bg=colors[key] or base_color(key))
                for c_, b_ in sw.items():
                    b_.configure(highlightbackground=P["ink"] if c_ == colors[key] else P["line"])

            def set_(c_):
                colors[key] = c_
                repaint()
            for c_ in swatches:
                b_ = tk.Button(r1, bg=c_, activebackground=c_, width=2, relief="flat", bd=0, cursor="hand2",
                               highlightthickness=2, command=lambda c_=c_: set_(c_))
                b_.pack(side="left", padx=(0, 5))
                sw[c_] = b_

            def other():
                res = colorchooser.askcolor(color=colors[key] or base_color(key), parent=win, title=title)
                if res and res[1]:
                    set_(res[1])
            make_btn(r2, "Інший…", other).pack(side="left", padx=(0, 6))
            make_btn(r2, "Скинути", lambda: set_("")).pack(side="left", padx=(0, 6))
            prev.pack(side="left")
            color_ui[key] = repaint
            repaint()

        color_row("surf", "Колір карток (і фон кнопок)", SURF_SWATCHES)
        color_row("bg", "Колір фону вікна", BG_SWATCHES)

        # Сповіщення
        head(R, "Сповіщення про нові оплати")
        nvars = {}
        notif = self.notify_cfg()
        for k, label, color in TYPES:
            row = tk.Frame(R, bg=P["bg"])
            row.pack(anchor="w")
            nvars[k] = tk.BooleanVar(value=notif.get(k, True))
            check(row, label, nvars[k]).pack(side="left")
            tk.Frame(row, bg=color, width=10, height=10).pack(side="left", padx=6)
        only_var = tk.BooleanVar(value=self.cfg.get("NOTIFY_ONLY_GROUP", False))
        sound_var = tk.BooleanVar(value=self.cfg.get("NOTIFY_SOUND", True))
        for var, label in ((only_var, "Лише для обраної групи"), (sound_var, "Звук")):
            check(R, label, var).pack(anchor="w")
        make_btn(R, "Тест звуку", lambda: play_pay_sound(self.root), height=26).pack(anchor="w", pady=(2, 0))

        tk.Label(R, text="Куди приходять сповіщення", bg=P["bg"], fg=P["ink"], font=FONT).pack(anchor="w", pady=(6, 2))
        pos_var = tk.StringVar(value=self.cfg.get("NOTIFY_POS", "br"))
        pgrid = tk.Frame(R, bg=P["bg"])
        pgrid.pack(anchor="w")
        for i, (val, label) in enumerate((("tl", "↖ Зверху ліворуч"), ("tr", "↗ Зверху праворуч"),
                                          ("bl", "↙ Знизу ліворуч"), ("br", "↘ Знизу праворуч"))):
            radio(pgrid, label, val, pos_var).grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 14))
        make_btn(R, "Показати тест", lambda: self.popup("Тест · PPV", "MoT: $19.91",
                                                        TYPE_COLOR["ppv"], pos_var.get())).pack(anchor="w", pady=(4, 0))

        head(R, "Оновлення")
        tk.Label(R, text=f"Версія {APP_VERSION}", bg=P["bg"], fg=P["muted"], font=FONT).pack(anchor="w")
        auto_upd_var = tk.BooleanVar(value=bool(self.cfg.get("AUTO_UPDATE", True)))
        check(R, "Перевіряти оновлення при запуску", auto_upd_var).pack(anchor="w")
        make_btn(R, "Перевірити оновлення", lambda: self.check_update(True, win)).pack(anchor="w", pady=(4, 0))

        head(R, "План з каналу (фото)")
        pentries = {}
        for k, label, default in (("PLAN_CHANNEL", "Частина назви каналу", "TiER2"), ("PLAN_TOPIC", "Гілка", "general"),
                                  ("TESSERACT_PATH", "Шлях до tesseract.exe (якщо не знайдено сам)", "")):
            tk.Label(R, text=label, bg=P["bg"], fg=P["ink"], font=FONT).pack(anchor="w", pady=(4, 0))
            e = make_entry(R, width=34)
            e.insert(0, str(self.cfg.get(k, default) or default))
            e.pack(anchor="w", ipady=4)
            pentries[k] = e

        result = {"ok": False}

        def save():
            vals = {k: e.get().strip() for k, e in entries.items()}
            if not all(vals.values()) or not vals["API_ID"].isdigit():
                messagebox.showinfo("Налаштування", "Заповніть усі поля Telegram (api_id має бути числом).", parent=win)
                return
            changed_tg = any(str(self.cfg.get(k, "")) != v for k, v in vals.items())
            self.cfg.update(vals)
            self.cfg["THEME"] = theme_var.get()
            self.cfg["ACCENT"] = chosen["accent"]
            self.cfg["COLORS"] = {k: v for k, v in colors.items() if v}
            pal_n = pal_var["n"]
            self.cfg["PALETTE"] = pal_n if (pal_n in PALETTES and PALETTES[pal_n]["theme"] == theme_var.get()) else "Класика"
            self.cfg["ANIM"] = bool(anim_var.get())
            self.cfg["SNOW"] = bool(snow_var.get())
            self.cfg["AUTO_UPDATE"] = bool(auto_upd_var.get())
            self.cfg["NOTIFY"] = {k: v.get() for k, v in nvars.items()}
            self.cfg["NOTIFY_ONLY_GROUP"] = only_var.get()
            self.cfg["NOTIFY_SOUND"] = sound_var.get()
            self.cfg["NOTIFY_POS"] = pos_var.get()
            for k, e in pentries.items():
                self.cfg[k] = e.get().strip()
            save_cfg(self.cfg)
            result["ok"] = True
            result["tg"] = changed_tg
            win.destroy()

        make_btn(win, "Зберегти", save, True).pack(anchor="e", pady=(14, 0))
        self.root.wait_window(win)
        if result["ok"]:
            self.apply_theme()
            if result.get("tg") and not first:
                messagebox.showinfo("Налаштування", "Дані Telegram змінено. Перезапустіть програму.")
        return result["ok"]

    # --- оновлення програми ---
    def check_update(self, manual=False, parent=None):
        """manual=True — користувач натиснув «Перевірити оновлення»; інакше тиха перевірка при запуску."""
        url = update_url(self.cfg)
        if not url:
            if manual:
                messagebox.showinfo("Оновлення", "Для цієї збірки не задано посилання на оновлення.\n"
                                    "Автор програми має вказати UPDATE_URL.", parent=parent or self.root)
            return
        if getattr(self, "_upd_busy", False):
            return
        self._upd_busy = True

        def work():
            try:
                info, err = fetch_update_info(url), None
            except Exception as e:
                info, err = None, e
            self.q.put(lambda: self._on_update_info(info, err, manual, parent))
        threading.Thread(target=work, daemon=True).start()

    def _on_update_info(self, info, err, manual, parent):
        self._upd_busy = False
        par = parent if (parent is not None and parent.winfo_exists()) else self.root
        if err is not None:
            log_error("update-check")
            if manual:
                messagebox.showerror("Оновлення", f"Не вдалося перевірити оновлення:\n{err}\n\nПеревірте інтернет і спробуйте ще раз.", parent=par)
            return
        if not info.get("version") or ver_tuple(info["version"]) <= ver_tuple(APP_VERSION):
            if manual:
                messagebox.showinfo("Оновлення", f"У вас остання версія ({APP_VERSION}).", parent=par)
            return
        frozen = getattr(sys, "frozen", False)
        link = info.get("url") if frozen else info.get("py_url")
        if not link:
            if manual:
                messagebox.showinfo("Оновлення", f"Вийшла версія {info['version']}, але файлу для вашого способу запуску "
                                    f"({'.exe' if frozen else 'pay_app.py'}) немає.", parent=par)
            return
        if not manual and self.cfg.get("SKIP_VERSION") == info["version"]:
            return
        notes = (info.get("notes") or "").strip()[:700]
        msg = (f"Доступна нова версія {info['version']} (у вас {APP_VERSION}).\n\n"
               + (notes + "\n\n" if notes else "") + "Оновити зараз? Програма перезапуститься, налаштування збережуться.")
        if messagebox.askyesno("Оновлення", msg, parent=par):
            self._download_update(info, link, par)
        elif not manual:
            self.cfg["SKIP_VERSION"] = info["version"]      # не набридаємо при кожному запуску
            self._save()

    def _download_update(self, info, link, par):
        frozen = getattr(sys, "frozen", False)
        dest = (sys.executable if frozen else os.path.abspath(__file__)) + ".new"
        win = tk.Toplevel(self.root)
        style_titlebar(win)
        win.title("Оновлення")
        win.configure(bg=P["bg"])
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", lambda: None)
        lbl = tk.Label(win, text="Завантаження оновлення…", bg=P["bg"], fg=P["ink"], font=FONT, padx=36, pady=24)
        lbl.pack()
        win.update_idletasks()
        win.geometry(f"+{self.root.winfo_rootx() + 60}+{self.root.winfo_rooty() + 120}")
        last = {"p": -1}

        def prog(got, total):
            p_ = got * 100 // total if total else got // 1048576
            if p_ != last["p"]:
                last["p"] = p_
                txt = f"Завантаження оновлення… {p_}%" if total else f"Завантаження оновлення… {p_} МБ"
                self.q.put(lambda: lbl.winfo_exists() and lbl.configure(text=txt))

        def work():
            try:
                download_file(link, dest, prog, info.get("sha256", ""))
                validate_download(dest, frozen)
                err = None
            except Exception as e:
                err = e
                try:
                    os.path.exists(dest) and os.remove(dest)
                except OSError:
                    pass
            self.q.put(lambda: self._finish_update(win, dest, err, frozen, par))
        threading.Thread(target=work, daemon=True).start()

    def _finish_update(self, win, dest, err, frozen, par):
        try:
            win.destroy()
        except tk.TclError:
            pass
        if err is None:
            try:
                args = apply_update(dest, frozen)
            except Exception as e:
                err = e
        if err is not None:
            log_error("update-apply")
            hint = ("\n\nПрограма лежить у папці без прав запису. Перенесіть її, наприклад, у «Документи»."
                    if isinstance(err, PermissionError) else "")
            messagebox.showerror("Оновлення", f"Оновити не вдалося:\n{err}{hint}", parent=par)
            return
        self.cfg.pop("SKIP_VERSION", None)
        try:
            _relaunch_later(args)
        except Exception:
            log_error("update-relaunch")
            messagebox.showinfo("Оновлення", "Оновлення встановлено. Запустіть програму знову.", parent=par)
        self.close()

    def close(self):
        try:
            self.cfg["GEOMETRY"] = self.root.geometry()
            save_cfg(self.cfg)
        except Exception:
            pass
        try:
            if self.client:
                asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.loop).result(timeout=3)
        except Exception:
            pass
        self.root.destroy()


SAMPLE = """💸 $24.89 · PPV у повідомленні
🔵 Модель: Master On Twinks 😈🖤⛓️ (OnlyFans)
💰 Чистими ≈ $19.91
💸 $5.00 · Нова підписка
🔵 Модель: Master On Twinks 😈🖤⛓️ (OnlyFans)
💸 $8.99 · Продовження підписки
🔵 Модель: Ben ryder (OnlyFans)
💸 $100.00 · чайові
🎁 BigWish · Модель: Benny
💸 $30.00 · PPV у повідомленні
🔵 Модель: Abelia (OnlyFans)
💰 Чистими ≈ $24.00"""


def main():
    if "--test" in sys.argv:
        pays = parse(SAMPLE)
        print([classify(p["kind"]) for p in pays])
        pr = parse("💸 $50.00 · чайові\n🎁 Para · Модель: Ben ryder\n💸 $50.00 · чайові\n🎁 Paradise · Модель: Ben ryder")
        print("para:", [(round(p["net"], 2), p["nocut"]) for p in pr], "(очікується [(41.5, True), (40.0, False)])")
        print("net:", [round(p["net"], 2) for p in pays], "(очікується 19.91 4.0 7.19 83.0 24.0)")
        d = datetime(2026, 11, 24, 0, 2, tzinfo=TZ)
        print(pay_dt(d, (23, 58)), pay_dt(d, (0, 1)), [shift_of(datetime(2026, 1, 1, h, 0, tzinfo=TZ)) for h in (0, 7, 8, 15, 16, 23)])
        t = [dict(p, dt=datetime(2026, 11, 24, h, 0, tzinfo=TZ)) for p, h in zip(pays, (1, 9, 10, 17, 20))]
        for sel in ({"night"}, {"morning"}, {"night", "evening"}, set(ALL_SHIFTS)):
            r = aggregate(t, "all", True, sel)
            print(sorted(sel), r["tc"], round(r["tn"], 2))
        print(report_text(aggregate(pays, "2"), "2", "2026-11-24"))
        apply_groups({"1": ["Alpha Girl", "mot"], "2": [], "5": ["beta"]})
        print("groups:", group_keys(), find_group("Alpha Girl 💋 (OnlyFans)"), find_group("MoT"), find_group("Beta x"), find_group("Zeta"))
        apply_groups(None)
        print("manual:", manual_total("2", date(2026, 11, 1), date(2026, 11, 24)), manual_total("all", date(2026, 11, 1), date(2026, 11, 24)),
              manual_total("2", date(2026, 11, 4), date(2026, 11, 24)))
        print("month:", parse_plan_text("23.11.2026 23:59:00\nREVENUE\n| TOTAL [TEAM 4 | $22 605 | $9 963| $21, $32 589,20, $42 509 | 89,85%| $47 313| 68,88% |"))
        print("plan text:", parse_plan_text("Model Shift plan Day plan Plan payments\nTOTAL $1 051,70 $2 103,40. $14 724"),
              parse_plan_text("REVENUE x\n| TOTAL [TEAM 4 | $22 605 | $9 963| $21, $32 589,20, $42 509 | 89,85%| $47 313| 68,88% |"))
        set_palette("dark", "#e5484d")
        print({k: P[k] for k in ("acc", "acc_d", "acc_ink", "acc_text", "sel")})
        now = datetime.now(TZ)
        print("ago:", [ago(now - timedelta(seconds=x)) for x in (5, 130, 3700, 90000)], money_short(100), money_short(19.91))
        print("sound bytes:", len(pay_sound_bytes()))
        return
    if tk is None:
        print("Потрібен tkinter (входить до стандартної установки Python).")
        return
    try:
        root = tk.Tk()
        App(root)
        root.mainloop()
    except Exception:
        with open(os.path.join(BASE, "pay_app_error.log"), "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
