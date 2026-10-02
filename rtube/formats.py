"""Розбір info від yt-dlp на зрозумілі варіанти: якість, доріжка, субтитри.

Те, що раніше робилося очима по таблиці `yt-dlp -F`: знайти рядок з
1080p mp4 «video only», знайти доріжку з [uk] і склеїти їхні ID через «+».

Вибір користувача зберігаємо не ID форматів, а «ключами» (висота + кодек,
мова доріжки): ID дубльованих доріжок мають вигляд 140-19, і номер після
дефіса залежить від порядку, у якому YouTube перелічив мови. Якщо доведеться
отримати info заново, ключі зіставляться з новим списком, а голі ID — ні.
"""

from dataclasses import dataclass, field

AUDIO_ONLY = "audio"   # ключ «відео» для режиму «лише звук»

# Порядок — від найсумісного: H.264 відтворює будь-який телевізор і
# плеєр, VP9 і AV1 стискають краще, але не скрізь працюють.
CODEC_ORDER = ("H.264", "VP9", "AV1", "HEVC", "інший")

LANG_NAMES = {
    "uk": "Українська", "en": "Англійська", "ru": "Російська", "pl": "Польська",
    "de": "Німецька", "fr": "Французька", "es": "Іспанська", "it": "Італійська",
    "pt": "Португальська", "ja": "Японська", "ko": "Корейська", "zh": "Китайська",
    "ar": "Арабська", "hi": "Гінді", "id": "Індонезійська", "iw": "Іврит",
    "he": "Іврит", "nl": "Нідерландська", "tr": "Турецька", "cs": "Чеська",
    "sk": "Словацька", "be": "Білоруська", "kk": "Казахська", "ro": "Румунська",
    "hu": "Угорська", "bg": "Болгарська", "el": "Грецька", "sv": "Шведська",
    "fi": "Фінська", "da": "Данська", "no": "Норвезька", "lt": "Литовська",
    "lv": "Латиська", "et": "Естонська", "ka": "Грузинська", "hy": "Вірменська",
    "az": "Азербайджанська", "vi": "В'єтнамська", "th": "Тайська",
    "bn": "Бенгальська", "ta": "Тамільська", "te": "Телугу", "ml": "Малаялам",
    "pa": "Пенджабі", "mr": "Маратхі", "ur": "Урду", "fa": "Перська",
    "ms": "Малайська", "fil": "Філіппінська", "sr": "Сербська", "hr": "Хорватська",
    "sl": "Словенська", "ca": "Каталанська",
}


def base_lang(code):
    return (code or "").split("-")[0].split("_")[0].lower()


_ALPHABET = "АБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ"


def sort_key(text):
    """Український алфавітний порядок: Python ставить «І» перед «А»."""
    return [(_ALPHABET.find(ch) if ch in _ALPHABET else 100 + ord(ch))
            for ch in (text or "").upper()]


def lang_name(code):
    """«uk» → «Українська», «en-US» → «Англійська (US)»."""
    if not code:
        return "Невідома мова"
    base = base_lang(code)
    name = LANG_NAMES.get(base, code)
    rest = code[len(base):].lstrip("-_")
    return f"{name} ({rest})" if rest and name != code else name


def codec_family(vcodec):
    v = (vcodec or "").lower()
    if v.startswith(("avc", "h264")):
        return "H.264"
    if v.startswith(("vp9", "vp09")):
        return "VP9"
    if v.startswith("av01"):
        return "AV1"
    if v.startswith(("hev", "hvc", "h265")):
        return "HEVC"
    return "інший"


def _size(fmt, duration=None):
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    if not size and duration and fmt.get("tbr"):
        size = fmt["tbr"] * 1000 / 8 * duration
    return int(size or 0)


def _is_video(f):
    return f.get("vcodec") not in (None, "none") and f.get("height")


def is_audio_only(f):
    # HLS-доріжки YouTube не мають acodec, але підписані «audio only» —
    # і саме в них є мітка dubbed-auto, тож пропускати їх не можна.
    if f.get("resolution") == "audio only":
        return True
    return f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")


def _proto_rank(f):
    # https (DASH) — суцільний файл, качається швидко й з відомим розміром;
    # m3u8 — шматками, беремо лише якщо іншого нема.
    return 0 if (f.get("protocol") or "").startswith("http") and "m3u8" not in (f.get("protocol") or "") else 1


# ── варіанти для інтерфейсу ──────────────────────────────────────────────

@dataclass
class VideoChoice:
    key: tuple            # (висота, fps-клас, кодек) або (AUDIO_ONLY,)
    label: str
    height: int = 0
    has_audio: bool = False   # прогресивний формат (відео+звук разом)


@dataclass
class AudioChoice:
    lang: str             # код мови як у yt-dlp, «» — без мови
    label: str
    is_original: bool = False
    kind: str = ""        # «оригінал» / «ШІ-дубляж» / «дубляж»


@dataclass
class SubChoice:
    key: tuple            # (мова, автоматичні?) або () — без субтитрів
    label: str


@dataclass
class Choices:
    videos: list = field(default_factory=list)
    audios: list = field(default_factory=list)
    subs: list = field(default_factory=list)
    default_video: int = 0
    default_audio: int = 0
    default_sub: int = 0


def _fps_class(f):
    return 60 if (f.get("fps") or 0) > 30 else 30


def _video_groups(info):
    """{(висота, fps, кодек): найкращий формат} для форматів лише-відео.

    Якщо таких немає (не YouTube, а сайт з одним файлом на якість) —
    беремо формати, де відео й звук разом.
    """
    formats = info.get("formats") or []
    pool = [f for f in formats if _is_video(f) and f.get("acodec") in (None, "none")]
    progressive = not pool
    if progressive:
        pool = [f for f in formats if _is_video(f)]
    groups = {}
    for f in pool:
        key = (int(f["height"]), _fps_class(f), codec_family(f.get("vcodec")))
        best = groups.get(key)
        rank = (-_proto_rank(f), f.get("tbr") or 0)
        if best is None or rank > (-_proto_rank(best), best.get("tbr") or 0):
            groups[key] = f
    return groups, progressive


def _audio_groups(info):
    """{мова: [аудіоформати, найкращі спершу]}."""
    groups = {}
    for f in info.get("formats") or []:
        if is_audio_only(f):
            groups.setdefault(f.get("language") or "", []).append(f)
    # «-drc» — версії YouTube зі «стабільною гучністю»: стиснутий динамічний
    # діапазон, тихе голосніше, гучне тихіше. Беремо їх лише за браком звичайних.
    for fmts in groups.values():
        fmts.sort(key=lambda f: (_proto_rank(f), "drc" in (f.get("format_id") or ""),
                                 -(f.get("abr") or f.get("tbr") or 0)))
    return groups


def _audio_kind(fmts):
    notes = " ".join((f.get("format_note") or "").lower() for f in fmts)
    if "original" in notes or any((f.get("language_preference") or 0) >= 10 for f in fmts):
        return True, "оригінал"
    if "dubbed-auto" in notes:
        return False, "ШІ-дубляж YouTube"
    if "dubbed" in notes:
        return False, "дубляж"
    # Дубляжі в https-форматах YouTube не підписує, а оригінал — завжди.
    return False, "дубляж" if any((f.get("language_preference") or 0) < 0 for f in fmts) else ""


def build_choices(info, max_height=1080, preferred_lang="uk"):
    """max_height 0 / None — без обмеження («Найкраща» в налаштуваннях);
    preferred_lang «orig» — оригінальна доріжка замість української."""
    duration = info.get("duration")
    choices = Choices()
    if not max_height:
        max_height = 10 ** 6

    # ── відео ──
    groups, progressive = _video_groups(info)
    # На кожну висоту+fps лишаємо один, найсумісніший кодек: 24 рядки
    # «1080p VP9 / 1080p AV1 / ...» лише заважали б вибрати якість.
    per_res = {}
    for (height, fps, codec), f in groups.items():
        cur = per_res.get((height, fps))
        if cur is None or CODEC_ORDER.index(codec) < CODEC_ORDER.index(cur[0]):
            per_res[(height, fps)] = (codec, f)
    for (height, fps), (codec, f) in sorted(per_res.items(), reverse=True):
        name = f"{height}p" + ("60" if fps == 60 else "")
        size = _size(f, duration)
        parts = [name, codec]
        if size:
            parts.append(("≈" if not f.get("filesize") else "") + format_size(size))
        choices.videos.append(VideoChoice((height, fps, codec), "  ·  ".join(parts),
                                          height, progressive))
    if _audio_groups(info):
        choices.videos.append(VideoChoice((AUDIO_ONLY,), "🎵  Лише звук (без відео)"))

    fitting = [i for i, v in enumerate(choices.videos) if v.height and v.height <= max_height]
    if fitting:
        choices.default_video = fitting[0]
    elif choices.videos:
        heights = [i for i, v in enumerate(choices.videos) if v.height]
        choices.default_video = heights[-1] if heights else 0

    # ── звук ──
    agroups = _audio_groups(info)
    audios = []
    single = len(agroups) == 1
    for lang, fmts in agroups.items():
        if single:
            # Єдина доріжка — це і є оригінал, хоч YouTube й не ставить їй
            # позначку «original». Мову вказує автор, і вона буває хибною
            # (україномовний ролик із «English»), тож «дубляжем» її не звемо.
            is_orig, kind = True, "єдина доріжка"
        else:
            is_orig, kind = _audio_kind(fmts)
        label = lang_name(lang) if lang else "Основна доріжка"
        if kind:
            label += f" — {kind}"
        audios.append(AudioChoice(lang, label, is_orig, kind))
    pref = base_lang(preferred_lang)
    audios.sort(key=lambda a: (base_lang(a.lang) != pref, not a.is_original, sort_key(a.label)))
    choices.audios = audios
    choices.default_audio = default_audio_index(audios, preferred_lang)

    # ── субтитри ──
    subs = [SubChoice((), "Без субтитрів")]
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    for lang in sorted(manual, key=lambda c: (base_lang(c) != pref, sort_key(lang_name(c)))):
        if lang == "live_chat":
            continue
        subs.append(SubChoice((lang, False), f"{lang_name(lang)} — від автора"))
    # Автоматичних мов у YouTube ~180 (машинний переклад на все підряд) —
    # показуємо лише корисні: українську, мову оригіналу та англійську.
    # «xx-orig» YouTube створює для КОЖНОЇ дубльованої доріжки, тож
    # лишаємо тільки той, що збігається з мовою справжнього оригіналу.
    orig_base = base_lang(original_lang(info) or info.get("language") or "")
    orig = [c for c in auto if c.endswith("-orig") and base_lang(c) == orig_base]
    picked = []
    for code in auto:
        if base_lang(code) == pref and not code.endswith("-orig"):
            picked.append((code, "автопереклад YouTube"))
    for code in orig:
        picked.append((code, "автосубтитри мовою оригіналу"))
    if "en" in auto and not any(base_lang(c) == "en" for c in orig):
        picked.append(("en", "автопереклад YouTube"))
    for code, note in picked:
        subs.append(SubChoice((code, True), f"{lang_name(code.removesuffix('-orig'))} — {note}"))
    choices.subs = subs
    choices.default_sub = default_sub_index(subs, audios, choices.default_audio, preferred_lang)
    return choices


def default_audio_index(audios, preferred_lang="uk"):
    """Українська, навіть якщо це ШІ-озвучка YouTube; інакше оригінал."""
    pref = base_lang(preferred_lang)
    for i, a in enumerate(audios):
        if base_lang(a.lang) == pref:
            return i
    for i, a in enumerate(audios):
        if a.is_original:
            return i
    return 0


def default_sub_index(subs, audios, audio_index, preferred_lang="uk"):
    """Субтитри потрібні, лише якщо української доріжки немає.

    Є українська озвучка — субтитри за замовчуванням вимкнені. Немає —
    беремо українські субтитри від автора. Автопереклад YouTube сам не
    вмикаємо: зараз він здебільшого відповідає 429 (перевірено 02.10.2026 —
    не допомагають ні повтори з паузою, ні curl_cffi), і користувач
    отримував «Готово, але без субтитрів». Обрати його можна вручну.
    """
    pref = base_lang(preferred_lang)
    if audios and base_lang(audios[audio_index].lang) == pref:
        return 0
    for i, s in enumerate(subs):
        if s.key and not s.key[1] and base_lang(s.key[0]) == pref:
            return i
    return 0


# ── вибір → рядок формату yt-dlp ─────────────────────────────────────────

def pick_audio_format(info, lang, prefer_ext="m4a"):
    fmts = _audio_groups(info).get(lang or "")
    if not fmts:
        return None
    same = [f for f in fmts if f.get("ext") == prefer_ext]
    # _proto_rank іде першим: m4a з m3u8 гірший за opus з https.
    best_same = same[0] if same else None
    if best_same and _proto_rank(best_same) <= _proto_rank(fmts[0]):
        return best_same
    return fmts[0]


def original_lang(info):
    for lang, fmts in _audio_groups(info).items():
        if _audio_kind(fmts)[0]:
            return lang
    return None


def resolve_format(info, video_key, audio_lang, container="mp4", keep_original=False,
                   audio_ext=None):
    """Рядок для параметра format, як «137+140-19» у cmd. None — нічого не підійшло."""
    if video_key and video_key[0] == AUDIO_ONLY:
        fmt = pick_audio_format(info, audio_lang, audio_ext or "m4a")
        return fmt["format_id"] if fmt else None

    groups, progressive = _video_groups(info)
    video = groups.get(tuple(video_key)) if video_key else None
    if video is None:
        # Ключ не зіставився (наприклад, після повторного отримання info
        # зник якийсь кодек) — беремо ту саму висоту з будь-яким кодеком.
        same_h = [(CODEC_ORDER.index(k[2]), f) for k, f in groups.items()
                  if video_key and k[0] == video_key[0] and k[1] == video_key[1]]
        if same_h:
            video = min(same_h, key=lambda x: x[0])[1]
    if video is None:
        return None
    if progressive:
        return video["format_id"]

    prefer = "m4a" if container == "mp4" else "webm"
    audio = pick_audio_format(info, audio_lang, prefer)
    if audio is None:
        return video["format_id"]
    ids = [video["format_id"], audio["format_id"]]
    if keep_original:
        orig = original_lang(info)
        if orig is not None and orig != audio_lang:
            extra = pick_audio_format(info, orig, prefer)
            if extra:
                ids.append(extra["format_id"])
    return "+".join(ids)


def format_size(num):
    num = float(num)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if num < 1024 or unit == "ГБ":
            return f"{num:.0f} {unit}" if unit in ("Б", "КБ") else f"{num:.1f} {unit}"
        num /= 1024
    return ""
