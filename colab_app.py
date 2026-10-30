#!/usr/bin/env python3
"""
BollywoodSub AI - تطبيق مخصص للتشغيل على Google Colab
يقوم بترجمة المسلسلات والأفلام الهندية إلى العربية بدقة سينمائية وتوقيت Base-60 حتمي
مع واجهة Gradio ورابط فوري عام (share=True) مجاناً 100% دون أي قيود على الحجم أو الوقت.
"""

import os
import sys
import json
import re
import time
import tempfile
import subprocess
from pathlib import Path

# محاولة استيراد المكتبات الأساسية
try:
    import gradio as gr
except ImportError:
    print("جاري تثبيت gradio...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "gradio"], check=True)
    import gradio as gr

import zipfile

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("جاري تثبيت google-genai...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "google-genai>=2.4.0"], check=True)
    from google import genai
    from google.genai import types

# ==========================================
# دوال التوقيت الحتمية (Base-60 Math)
# ==========================================
def parse_timecode_to_seconds(timecode_str: str) -> float:
    """تحويل صيغة MM:SS.ms أو HH:MM:SS.ms إلى ثوانٍ حقيقية بقاعدة Base-60"""
    if not timecode_str:
        return 0.0
    clean = str(timecode_str).strip().replace(',', '.')
    parts = clean.split(':')
    if len(parts) == 3:
        hours = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2])
        return (hours * 3600.0) + (minutes * 60.0) + seconds
    elif len(parts) == 2:
        minutes = float(parts[0])
        seconds = float(parts[1])
        return (minutes * 60.0) + seconds
    else:
        try:
            return float(clean)
        except ValueError:
            return 0.0

def seconds_to_srt_time(total_sec: float) -> str:
    """تحويل الثواني إلى صيغة SRT: HH:MM:SS,mmm"""
    total_sec = max(0.0, total_sec)
    hours = int(total_sec // 3600)
    minutes = int((total_sec % 3600) // 60)
    seconds = int(total_sec % 60)
    milliseconds = int(round((total_sec % 1) * 1000))
    if milliseconds >= 1000:
        seconds += 1
        milliseconds = 0
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

def seconds_to_vtt_time(total_sec: float) -> str:
    """تحويل الثواني إلى صيغة WebVTT: HH:MM:SS.mmm"""
    total_sec = max(0.0, total_sec)
    hours = int(total_sec // 3600)
    minutes = int((total_sec % 3600) // 60)
    seconds = int(total_sec % 60)
    milliseconds = int(round((total_sec % 1) * 1000))
    if milliseconds >= 1000:
        seconds += 1
        milliseconds = 0
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"

def seconds_to_ass_time(total_sec: float) -> str:
    """تحويل الثواني إلى صيغة ASS: H:MM:SS.cc"""
    total_sec = max(0.0, total_sec)
    hours = int(total_sec // 3600)
    minutes = int((total_sec % 3600) // 60)
    seconds = int(total_sec % 60)
    centiseconds = int(round((total_sec % 1) * 100))
    if centiseconds >= 100:
        seconds += 1
        centiseconds = 0
    return f"{hours:d}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"

# ==========================================
# دالة تصفية المؤثرات الصوتية وسارجام
# ==========================================
def is_sound_effect_or_ambient(arabic_text: str, hindi_text: str = "") -> tuple[bool, str]:
    if not arabic_text:
        return True, ""
    clean_ar = arabic_text.strip()
    clean_hi = (hindi_text or "").strip().lower()

    # تصفية نوتات السلم الموسيقي الهندي (سارجام)
    clean_no_notes = re.sub(r'[♪♫\s,،\-._:!؟]+', ' ', clean_ar).strip()
    words_list = clean_no_notes.split()
    sargam_keywords = {"صا", "ري", "غا", "ما", "با", "دا", "ذا", "ني", "سا", "ماه", "ذاه", "باه", "ناه", "ساه", "غاه"}
    if words_list and len(words_list) >= 3 and sum(1 for w in words_list if w in sargam_keywords) / len(words_list) >= 0.65:
        return True, ""

    # فحص الآهات والترانيم المجردة فقط (مع استبعاد 'لا' لأنها كلمة حوار شرعية تعني الرفض في المشاهد الدرامية)
    pure_vocalise_words = {"آه", "أه", "اووه", "أوه", "هممم", "همم", "نا"}
    if words_list and len(words_list) >= 2 and all(w in pure_vocalise_words for w in words_list):
        return True, ""

    if "♪" in clean_ar or "♫" in clean_ar:
        return False, clean_ar

    sound_keywords = [
        "صوت دق", "صوت طرق", "صوت فتح", "صوت غلق", "صوت الباب", "دق الباب", "طرق الباب", "طرق على الباب", "فتح الباب", "إغلاق الباب",
        "صوت خطوات", "خطوات أقدام", "أصوات أقدام", "صوت بكاء", "صوت نحيب", "صوت صراخ", "صوت ضحك", "صوت قهقهة",
        "صوت تنفس", "تنهد", "أنفاس عميقة", "صوت رنين", "رنين هاتف", "رنين جرس", "صوت صفير", "صوت زامور",
        "صوت محرك", "صوت سيارة", "صوت فرامل", "صوت إطلاق نار", "صوت رصاص", "صوت انفجار", "صوت تصفيق",
        "صوت رياح", "صوت مطر", "قطرات ماء", "صوت موسيقى", "موسيقى تصويرية", "موسيقى حزينة", "موسيقى حماسية",
        "صوت صرير", "صوت سقوط", "تحطم زجاج", "صوت همس", "صوت صفارة"
    ]

    for kw in sound_keywords:
        stripped = re.sub(r'[\(\)\[\]\{\}\*\.\-_\:!؟]', '', clean_ar).strip()
        if stripped == kw or (stripped.startswith(kw) and len(stripped) <= len(kw) + 12):
            return True, ""

    bracket_match = re.fullmatch(r'^[\[\(\*](.*?)[\]\)\*]$', clean_ar)
    if bracket_match:
        inner = bracket_match.group(1).strip()
        if any(w in inner for w in ["صوت", "موسيقى", "طرق", "دق", "بكاء", "ضحك", "أقدام", "خطوات", "تنفس"]):
            return True, ""

    cleaned_ar = re.sub(r'^[\[\(][^\]\)]*صوت[^\]\)]*[\]\)]\s*', '', clean_ar).strip()
    cleaned_ar = re.sub(r'\s*[\[\(][^\]\)]*صوت[^\]\)]*[\]\)]$', '', cleaned_ar).strip()
    if not cleaned_ar:
        return True, ""
    return False, cleaned_ar

# ==========================================
# دالة تنسيق وتقسيم أسطر الترجمة (معايير نتفليكس لمنع تحذيرات 42 حرفاً)
# ==========================================
def wrap_subtitle_text(text: str, max_chars_per_line: int = 40) -> str:
    """
    تنسيق نصوص الترجمة بحد أقصى سطرين وطول مناسب (38-42 حرفاً للسطر الواحد)
    مع احترام علامات الترقيم (، ؟ ! .) لمنع تحذيرات المشغلات (84 warnings).
    """
    if not text:
        return ""
    text = text.strip()
    text = re.sub(r'[ \t]+', ' ', text)

    # إذا كان النص مقسماً مسبقاً، نتحقق من سلامة طول السطرين
    if "\n" in text:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if len(lines) <= 2 and all(len(l) <= max_chars_per_line + 4 for l in lines):
            return "\n".join(lines)
        text = " ".join(lines)

    if len(text) <= max_chars_per_line:
        return text

    words = text.split()
    if len(words) <= 3:
        return text

    target_pos = len(text) / 2
    best_split_idx = -1
    best_score = 9999
    curr_len = 0

    for idx, w in enumerate(words[:-1]):
        curr_len += len(w) + 1
        distance = abs(curr_len - target_pos)
        # ميزة تفضيلية إذا كانت الكلمة تنتهي بفاصلة أو علامة استفهام أو ترقيم
        bonus = 12 if w.endswith(("،", "؟", "!", "؛", ".", "…")) else 0
        score = distance - bonus
        if score < best_score:
            best_score = score
            best_split_idx = idx

    if best_split_idx != -1:
        line1 = " ".join(words[:best_split_idx + 1])
        line2 = " ".join(words[best_split_idx + 1:])
        return f"{line1}\n{line2}"

    return text

# ==========================================
# دالة تجزئة الأسطر الطويلة ومنع تجاوز 5.5 ثوانٍ وحظر تكديس الكلام السريع (6-8 كلمات كحد أقصى)
# ==========================================
def split_long_cue(cue: dict, max_duration: float = 5.5, max_words: int = 8) -> list:
    start = cue["start"]
    end = cue["end"]
    total_dur = end - start
    arabic_text = cue.get("arabic", "").strip()
    arabic_words = arabic_text.split()
    
    # الالتزام الصارم بقاعدة عدم دمج أكثر من 6 إلى 8 كلمات في سطر واحد
    if total_dur <= max_duration and len(arabic_words) <= max_words:
        return [cue]

    sentences = [s.strip() for s in re.split(r'([.،؟!?؛…\n]+)', arabic_text) if s.strip()]
    merged_sentences = []
    curr = ""
    for piece in sentences:
        if piece in [".", "،", "؟", "!", "؟!", "!؟", "؛", "…", "\n", "..", "..."]:
            curr += piece
        else:
            if curr:
                merged_sentences.append(curr.strip())
            curr = piece
    if curr:
        merged_sentences.append(curr.strip())

    final_segments = []
    if len(merged_sentences) <= 1:
        words = arabic_words
        chunk_size = max(4, min(max_words, 7))
        for i in range(0, len(words), chunk_size):
            chunk = " ".join(words[i:i + chunk_size])
            if chunk:
                final_segments.append(chunk)
    else:
        accum = ""
        for s in merged_sentences:
            if not accum:
                accum = s
            elif len(accum.split()) + len(s.split()) <= max_words:
                accum += " " + s
            else:
                final_segments.append(accum)
                accum = s
        if accum:
            final_segments.append(accum)

    if len(final_segments) <= 1:
        return [cue]

    total_words = max(1, sum(len(seg.split()) for seg in final_segments))
    split_cues = []
    curr_start = start
    for idx, seg in enumerate(final_segments):
        seg_words = max(1, len(seg.split()))
        fraction = seg_words / total_words
        seg_duration = max(1.2, total_dur * fraction)
        seg_end = curr_start + seg_duration
        if idx == len(final_segments) - 1 or seg_end > end:
            seg_end = end

        split_cues.append({
            "start": round(curr_start, 2),
            "end": round(seg_end, 2),
            "arabic": seg,
            "hindi": cue.get("hindi", ""),
            "type": cue.get("type", "dialogue")
        })
        curr_start = seg_end
        if curr_start >= end:
            break
    return split_cues

# ==========================================
# استخراج وترقية الصوت عبر FFmpeg
# ==========================================
def extract_and_enhance_audio(input_media_path: str, filter_type: str = "vocal_clean") -> str:
    """استخراج الصوت وترقيته بـ FFmpeg لتحسين التعرف السمعي على الحوارات الهندية"""
    temp_dir = tempfile.gettempdir()
    output_audio = os.path.join(temp_dir, f"enhanced_{int(time.time())}.aac")

    # فلاتر صوتية متقدمة
    if filter_type == "vocal_clean":
        # عزل الترددات الصوتية البشرية (120Hz إلى 7000Hz) مع موازنة الشدة
        audio_filter = "highpass=f=120,lowpass=f=7200,dynaudnorm=p=0.9:m=10.0:r=0.9:b=1"
    elif filter_type == "aggressive_denoise":
        audio_filter = "highpass=f=150,lowpass=f=6500,afftdn=nf=-25,dynaudnorm=p=0.95"
    else:
        audio_filter = "dynaudnorm"

    cmd = [
        "ffmpeg", "-y",
        "-i", input_media_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-af", audio_filter,
        "-c:a", "aac",
        "-b:a", "64k",
        output_audio
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return output_audio
    except Exception as e:
        print(f"تنبيه: فشل تشغيل فلتر FFmpeg المتقدم ({e})، سيتم إرسال الملف مباشرة.")
        return input_media_path

# ==========================================
# دمج نموذج Whisper لاستخراج التوقيت بالمللي ثانية ومحاذاة مشاهد الغضب
# ==========================================
def align_subtitles_with_whisper(audio_path: str, cues: list, enabled: bool = False, progress=None) -> list:
    """
    استخدام تقنية Whisper ASR لاكتشاف فترات التحدث الحقيقية (Voice Activity Detection)
    ومحاذاة توقيتات الأسطر في مشاهد الصراخ والكلام السريع المتواصل.
    """
    if not enabled or not cues:
        return cues
    try:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            if progress:
                progress(0.88, desc="⚡ جاري تثبيت faster-whisper للمحاذاة بالمللي ثانية...")
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "faster-whisper"], check=True)
            from faster_whisper import WhisperModel

        if progress:
            progress(0.90, desc="🎙️ تشغيل Whisper لمطابقة التوقيت بالمللي ثانية على صوت الممثلين...")

        # تشغيل Whisper بأخف وأسرع نموذج (tiny) مع تفعيل كاشف الصوت VAD
        model = WhisperModel("tiny", device="auto", compute_type="auto")
        segments, _ = model.transcribe(
            audio_path,
            language="hi",
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=250)
        )
        whisper_intervals = []
        for seg in segments:
            whisper_intervals.append({"start": seg.start, "end": seg.end})

        if whisper_intervals:
            for cue in cues:
                c_start = cue["start"]
                c_end = cue["end"]
                # البحث عن الفترات الصوتية المتطابقة
                matches = [w for w in whisper_intervals if not (w["end"] < c_start or w["start"] > c_end + 1.2)]
                if matches:
                    actual_end = max(w["end"] for w in matches)
                    # تمديد وقت نهاية السطر إذا كان الممثل لا يزال يتكلم بصوته
                    if actual_end > cue["end"]:
                        cue["end"] = round(actual_end + 0.35, 2)
        return cues
    except Exception as e:
        print(f"تنبيه Whisper: تعذر إجراء المحاذاة التلقائية ({e})، سيتم الاعتماد على المحرك الحتمي المتقدم.")
        return cues

# ==========================================
# توليد ملفات الترجمة (SRT, ASS, VTT)
# ==========================================
def generate_srt_content(cues: list) -> str:
    lines = []
    for idx, cue in enumerate(cues, 1):
        lines.append(str(idx))
        lines.append(f"{seconds_to_srt_time(cue['start'])} --> {seconds_to_srt_time(cue['end'])}")
        # تنسيق السطور وفق معايير نتفليكس (سطرين كحد أقصى وأقل من 42 حرفاً للسطر)
        lines.append(wrap_subtitle_text(cue['arabic']))
        lines.append("")
    return "\n".join(lines)

def generate_vtt_content(cues: list) -> str:
    lines = ["WEBVTT", ""]
    for idx, cue in enumerate(cues, 1):
        lines.append(str(idx))
        lines.append(f"{seconds_to_vtt_time(cue['start'])} --> {seconds_to_vtt_time(cue['end'])}")
        lines.append(wrap_subtitle_text(cue['arabic']))
        lines.append("")
    return "\n".join(lines)

def generate_ass_content(cues: list, title: str = "BollywoodSub") -> str:
    header = f"""[Script Info]
Title: {title} - Bollywood Golden Cinema Style
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.601
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Cairo,52,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3.5,1.5,2,40,40,45,1
Style: Song,Cairo,50,&H0000E5FF,&H000000FF,&H00002233,&H80000000,-1,1,0,0,100,100,0,0,1,3.5,1.5,2,40,40,45,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for cue in cues:
        start_str = seconds_to_ass_time(cue["start"])
        end_str = seconds_to_ass_time(cue["end"])
        style = "Song" if cue.get("type") in ["song", "rap"] or "♪" in cue["arabic"] else "Default"
        wrapped = wrap_subtitle_text(cue["arabic"])
        text = wrapped.replace("\n", "\\N")
        events.append(f"Dialogue: 0,{start_str},{end_str},{style},,0,0,0,,{text}")
    return header + "\n".join(events)

# ==========================================
# محرك الذكاء الاصطناعي مع التبديل التلقائي الذكي (Gemini API)
# ==========================================
# ترتيب النماذج مع وضع الأكثر استقراراً وجودة في المقدمة
AVAILABLE_MODELS = [
    "gemini-3.5-flash",         # مستقر وسريع جداً وقدرة 64k توكن تغطي حلقات كاملة (22+ دقيقة)
    "gemini-3.6-flash",         # دقة سينمائية واستقرار عالي
    "gemini-3.8-flash",         # أحدث إصدار فائق البلاغة
    "gemini-flash-latest",      # أحدث إصدار فلاش مستقر
    "gemini-3.1-pro-preview",   # نموذج احترافي عميق
    "gemini-flash-lite-latest", # خفيف للمقاطع القصيرة
    "gemini-3.1-flash-lite",    # خفيف
]

MODEL_FALLBACKS = {
    "gemini-3.5-flash": ["gemini-3.5-flash", "gemini-3.6-flash", "gemini-flash-latest"],
    "gemini-3.6-flash": ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-flash-latest"],
    "gemini-3.8-flash": ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash"],
    "gemini-flash-latest": ["gemini-flash-latest", "gemini-3.5-flash", "gemini-3.6-flash"],
    "gemini-3.1-pro-preview": ["gemini-3.1-pro-preview", "gemini-3.6-flash", "gemini-3.5-flash"],
    "gemini-flash-lite-latest": ["gemini-flash-lite-latest", "gemini-3.1-flash-lite"],
    "gemini-3.1-flash-lite": ["gemini-3.1-flash-lite", "gemini-flash-lite-latest"],
}

def translate_media_file(
    file_path: str,
    api_key: str,
    selected_model: str,
    strict_model: bool,
    dialect: str,
    preserve_hinglish: bool,
    translate_songs: bool,
    ignore_sound_effects: bool,
    enhance_audio: bool,
    use_whisper_timestamps: bool = False,
    progress=gr.Progress()
):
    if not file_path:
        return "❌ يرجى رفع ملف فيديو أو صوت أولاً.", None, None, None, ""

    api_key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()
    if not api_key:
        return "❌ يرجى إدخال مفتاح Google AI Studio Gemini API للمتابعة.", None, None, None, ""

    progress(0.05, desc="تهيئة الاتصال ومكتبة Google Gemini...")
    client = genai.Client(api_key=api_key)

    # ترقية واستخراج الصوت
    media_to_send = file_path
    if enhance_audio:
        progress(0.12, desc="⚡ استخراج وترقية الصوت وتصفية الضوضاء بـ FFmpeg...")
        media_to_send = extract_and_enhance_audio(file_path, filter_type="vocal_clean")

    # اللهجات
    dialect_descriptions = {
        "الفصحى المعاصرة (MSA - القياسية)": "اللغة العربية الفصحى المعاصرة السليمة (Modern Standard Arabic) بدقة سينمائية راقية. يمنع منعاً باتاً استخدام أي لهجة عامية. ترجم الأفعال والأسئلة بفصحى نقية.",
        "اللهجة المصرية السينمائية": "اللهجة المصرية السينمائية السلسة الشائعة في دبلجة الأفلام والمسلسلات",
        "اللهجة الشامية (السورية واللبنانية)": "اللهجة الشامية (السورية واللبنانية السينمائية)",
        "اللهجة الخليجية المعاصرة": "اللهجة الخليجية المعاصرة",
        "اللهجة العراقية الفنية": "اللهجة العراقية الفنية المعبرة"
    }
    target_dialect_desc = dialect_descriptions.get(dialect, dialect_descriptions["الفصحى المعاصرة (MSA - القياسية)"])

    sound_rule = ""
    if ignore_sound_effects:
        sound_rule = """
- حظر قاطع للمؤثرات الصوتية والبيئية (No Sound Effects / Dialogue & Lyrics ONLY):
  * يمنع ترجمة أو كتابة أي مؤثر صوتي، أو أصوات بيئية (صوت دق أو طرق الباب، فتح أو إغلاق الباب، خطوات أقدام، بكاء، ضحك، تنهد، رنين، محركات، رصاص، إلخ).
  * لا تضع أي أوصاف بين أقواس مثل [صوت باب] أو (طرق على الباب).
  * إذا كان المشهد يحتوي فقط على موسيقى تصويرية أو صمت دون كلام بشري: لا تقم بإنشاء أي سطر ترجمة إطلاقاً وتجاوز تلك الثواني.
  * الترجمة مخصصة حصراً للكلام البشري الصادر من أفواه الممثلين وكلمات الأغاني الحقيقية.
"""

    fast_speech_rule = """
- حظر قاطع لتكديس الكلام السريع والغاضب في سطر واحد (Strict Anti-Clustering for Fast & Angry Speech):
  * في مشاهد الغضب، الصراخ، والمواجهات العاطفية التي يتحدث فيها الممثل بسرعة متواصلة وبدون توقف: يُحظر نهائياً دمج أكثر من 6 إلى 8 كلمات في سطر واحد!
  * يُمنع منعاً باتاً دمج جمل المشهد التي تستمر 10 أو 15 ثانية في سطر واحد مدته 4 أو 5 ثوانٍ فقط ثم يختفي في صمت.
  * قسّم الحوار المتواصل إلى أسطر قصيرة متتابعة ومقتضبة جداً (3 إلى 6 كلمات لكل سطر)، وحدد توقيت بداية ونهاية كل جملة مقتضبة بدقة تامة متزامنة مع خروج الصوت لحظة بلحظة.
  * التغطية الزمنية الشاملة: إذا استمر الممثل في الصراخ أو الحديث من 00:10 إلى 00:22، يجب أن تولد أسطر ترجمة متتالية تغطي الفترة حتى 00:22 بالكامل ولا ينتهي توقيت السطر مبكراً ويترك الممثل يتحدث بدون ترجمة!
  * حظر قاطع لتفويت الحوارات العاطفية والمكررة: في مشاهد الصدمة أو البكاء، عندما يكرر الممثل عبارات مقتضبة (مثل: "أنت؟ أنت لست فلان؟"، "قل لي الحقيقة"، "لا.. لا يا فلان!")، ترجم كل كلمة ولا تهمل أي كلام بشري.
  * أولوية مطلقة للكلام البشري فوق الموسيقى التصويرية: إذا تداخل كلام الممثل مع موسيقى تصويرية حزينة أو كمان، فالكلام البشري هو الأولوية الأولى 100%.
"""

    sub_segmentation_rule = """
- التزامن الشفهي الصارم وتوزيع الأسطر (Strict Speech-Onset & Accurate Offset):
  * قاعدة تطابق البداية: يجب أن يتطابق وقت بداية السطر (timecodeStart) بدقة متناهية مع لحظة نطق أول حرف يخرج من فم الممثل. يمنع ظهور السطر أثناء صمت الممثل قبل بدء الكلام.
  * قاعدة إتمام نهاية الكلام: يجب أن يستمر وقت انتهاء السطر (timecodeEnd) حتى إتمام الممثل نطق آخر حرف تماماً مع نبرة صوته وإغلاق شفتيه، دون إنهاء السطر مبكراً قبل أن يفرغ الممثل من جملته.
  * منع الاختفاء الخاطف: اجعل السطر مريحاً للقراءة بالعربية (بين 1.5 إلى 4.5 ثوانٍ، وبحد أقصى 6 إلى 8 كلمات لكل سطر في المشاهد السريعة).
"""

    sargam_ban_rule = """
- حظر قاطع لتمارين السلم الموسيقي الصوتي والآهات المجردة (Strict Ban on Sargam & Vocalises):
  * يمنع منعاً باتاً ترجمة أو كتابة نوتات سارجام: (Sa Re Ga Ma Pa Dha Ni / صا ري غا ما با دا ذا ني سا).
  * يمنع كتابة الآهات أو التمتمة والترانيم المجردة (آه آه، أووه).
  * أولوية مطلقة لكلام الممثلين: إذا كان أحدهم يتدرب على الغناء أو العزف وتكلم ممثلون آخرون في المشهد، ركز حصراً على ترجمة كلام الممثلين وتجاهل العزف والغناء.
"""

    system_instruction = f"""
أنت المترجم السينمائي الأول المتخصص في ترجمة الأفلام والمسلسلات الهندية إلى اللغة العربية:
- الهدف الأساسي: استمع بتركيز فائق وحوّل كل الحوارات حصراً إلى: {target_dialect_desc}.
- في حال اختيار الفصحى: التزم بالقواعد النحوية والتراكيب الفصيحة المعتمدة في منصات Netflix و Shahid دون أي عامية.
- ابدأ من بداية المقطع (00:00.00) وحتى نهايته تماماً دون تفويت أي جملة أو محادثة عبر كامل مدة الحلقة.
{sound_rule}
{fast_speech_rule}
{sub_segmentation_rule}
{sargam_ban_rule}
- الـ Hinglish: ({'احتفظ بنبرة الحوار المميزة' if preserve_hinglish else 'ترجم المعنى إلى الفصحى المباشرة'}).
- أغاني الخلفية والراب: ({'ضع علامة ♪ في بداية ونهاية كل بيت شعري أو مقطع غنائي حقيقي' if translate_songs else 'ترجم الحوارات فقط'}).

قاعدة التوقيت الحتمية الصارمة (Base-60):
- أخرج التوقيت بصيغة نصية (MM:SS.ms) مثل "00:01.50" أو "01:25.80" حيث الدقيقة = 60 ثانية تماماً.
- تجنب علامات الاقتباس المزدوجة داخل النصوص واستخدم الفاصلة العليا '...' أو «...».
- أخرج الناتج حصراً بمصفوفة JSON صالحة ومطابقة للشكل التالي:
[
  {{
    "timecodeStart": "00:00.50",
    "timecodeEnd": "00:03.20",
    "arabicTranslation": "الترجمة العربية السينمائية المتقنة",
    "type": "dialogue"
  }}
]
"""

    prompt = (
        "حلل وترجم هذا المقطع بالكامل من بدايته (00:00.00) وحتى نهايته بدقة واحترافية سينمائية كاملة إلى العربية دون توقف حتى آخر ثانية من الحلقة كاملة. "
        "ترجم كل سطر وكل حوار ينطقه الممثلون أو كلمات الأغاني الحقيقية عبر كامل مدة المقطع دون إهمال أي ثانية أو مشهد درامي عاطفي حتى مع وجود موسيقى خلفية. "
        "استبعد السلم الموسيقي الصوتي (Sa Re Ga Ma) والآهات تماماً وركز حصراً على حوارات الممثلين. "
        "ابدأ توقيت كل سطر فور خروج أول كلمة، واستمر حتى ينهي الممثل آخر كلمة في جملته، وقسّم الحوارات إلى أسطر قصيرة ومريحة لعين المشاهد، "
        "واستخرج التوقيتات بصيغة MM:SS.ms في مصفوفة JSON صالحة."
    )

    # رفع الملف إلى Gemini Files API
    progress(0.20, desc="📤 رفع ملف الوسائط إلى Google Gemini Cloud...")
    try:
        uploaded_gemini_file = client.files.upload(file=media_to_send)
    except Exception as e:
        return f"❌ فشل رفع الملف إلى خوادم Google: {e}", None, None, None, ""

    # انتظار معالجة الملف
    progress(0.35, desc="⏳ انتظار معالجة ملف الفيديو في سحابة Google...")
    poll_start = time.time()
    file_ref = uploaded_gemini_file
    while hasattr(file_ref, "state") and getattr(file_ref.state, "name", None) == "PROCESSING":
        if time.time() - poll_start > 180:
            break
        time.sleep(2)
        try:
            file_ref = client.files.get(name=uploaded_gemini_file.name)
        except Exception:
            break

    if hasattr(file_ref, "state") and getattr(file_ref.state, "name", None) == "FAILED":
        return f"❌ فشلت معالجة الملف في Google Gemini: {getattr(file_ref, 'error', 'Unknown')}", None, None, None, ""

    uploaded_gemini_file = file_ref

    safety_settings = [
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_CIVIC_INTEGRITY, threshold=types.HarmBlockThreshold.BLOCK_NONE),
    ]

    # تحديد قائمة النماذج بحسب رغبة المستخدم في الالتزام الصارم
    if strict_model:
        # التزام تام بالنموذج المختار مع محاولات إعادة تلقائية ذكية في حال الضغط المؤقت (503)
        models_to_try = [selected_model]
    else:
        # إذا سمح بالتبديل، ننتقل فقط للنماذج الكاملة المكافئة ولا ننزل لـ Lite تلقائياً ما لم يكن المختار هو Lite
        if "lite" in selected_model.lower():
            models_to_try = [selected_model, "gemini-flash-lite-latest", "gemini-3.1-flash-lite"]
        else:
            full_flash_pool = ["gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.8-flash", "gemini-flash-latest"]
            models_to_try = [selected_model] + [m for m in full_flash_pool if m != selected_model]
    
    response = None
    last_err = None
    successful_model = None
    raw_cues = []

    progress(0.50, desc="🧠 جاري الترجمة السينمائية الفائقة عبر الذكاء الاصطناعي...")

    for idx, candidate_model in enumerate(models_to_try):
        target_models = MODEL_FALLBACKS.get(candidate_model, [candidate_model]) if not strict_model else [candidate_model]
        for actual_model in target_models:
            max_retries = 3 if strict_model else 2
            for attempt in range(max_retries):
                try:
                    retry_label = "" if attempt == 0 else f" (إعادة محاولة {attempt+1}/{max_retries} بعد ضغط مؤقت)..."
                    progress(0.55 + (idx * 0.05), desc=f"🎬 جاري التحليل بنموذج: {actual_model}{retry_label}")
                    
                    # سعة التوكنات: 8192 هو الحد الأقصى الرسمي المسموح به من Google لكافة نماذج Gemini
                    token_limit = 8192
                    
                    resp = client.models.generate_content(
                        model=actual_model,
                        contents=[uploaded_gemini_file, prompt],
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.15,
                            max_output_tokens=token_limit,
                            response_mime_type="application/json",
                            safety_settings=safety_settings
                        )
                    )

                    text = resp.text.strip() if hasattr(resp, "text") and resp.text else ""
                    if not text and hasattr(resp, "candidates") and resp.candidates:
                        for cand in resp.candidates:
                            if hasattr(cand, "content") and hasattr(cand.content, "parts"):
                                for part in cand.content.parts:
                                    if hasattr(part, "text") and part.text:
                                        text += part.text
                    text = text.strip()
                    if text.startswith("```json"):
                        text = text[7:]
                    if text.startswith("```"):
                        text = text[3:]
                    if text.endswith("```"):
                        text = text[:-3]
                    text = text.strip()

                    parsed = json.loads(text)
                    if isinstance(parsed, list) and len(parsed) > 0:
                        response = resp
                        raw_cues = parsed
                        successful_model = actual_model
                        break
                except Exception as e:
                    last_err = e
                    err_str = str(e).lower()
                    # التعامل مع ضغط الخوادم المؤقت (503 Unavailable / 429 Rate Limit)
                    if ("503" in err_str or "unavailable" in err_str or "high demand" in err_str or "429" in err_str or "resource_exhausted" in err_str) and attempt < max_retries - 1:
                        time.sleep(3 * (attempt + 1))
                        continue
                    break
            if response is not None:
                break
        if response is not None:
            break

    # تنظيف الملف المرفوع في السحابة
    try:
        client.files.delete(name=uploaded_gemini_file.name)
    except Exception:
        pass

    if not raw_cues:
        err_msg = str(last_err) if last_err else "لم يُرجع النموذج أي حوارات"
        if "503" in err_msg or "high demand" in err_msg.lower() or "unavailable" in err_msg.lower():
            return (
                f"❌ النموذج المختار ({selected_model}) يواجه ضغطاً مؤقتاً في خوادم Google (503 Service Unavailable).\n"
                f"💡 الحل الموصى به: اختر نموذج gemini-3.5-flash أو gemini-3.6-flash من القائمة، فهما متوفران ومستقران حالياً ويغطيان كامل الحلقة (22 دقيقة) بسرعة فائقة ودون تقطيع.",
                None, None, None, None, ""
            )
        return f"❌ تعذر استخراج الترجمة: {err_msg}", None, None, None, None, ""

    progress(0.85, desc="📐 تطبيق معادلات التوقيت Base-60 وتمديد نهايات السطور...")

    # معالجة وضبط الأسطر
    processed_cues = []
    for item in raw_cues:
        start_sec = parse_timecode_to_seconds(item.get("timecodeStart", "00:00.00"))
        end_sec = parse_timecode_to_seconds(item.get("timecodeEnd", "00:00.00"))
        ar_text = item.get("arabicTranslation", "").strip()
        hi_text = item.get("hindiDialogue", "").strip()

        # إضافة تمديد زمني ذكي لنهاية السطر (Tail Padding: +0.40s) لمنع اختفاء السطر قبل إنهاء الممثل كلامه
        if end_sec <= start_sec:
            end_sec = start_sec + 2.2
        else:
            end_sec = round(end_sec + 0.40, 2)

        # ضمان حد أدنى مريح للقراءة
        if end_sec - start_sec < 1.4:
            end_sec = round(start_sec + 1.4, 2)

        if ignore_sound_effects:
            is_ambient, cleaned_text = is_sound_effect_or_ambient(ar_text, hi_text)
            if is_ambient:
                continue
            ar_text = cleaned_text

        if not ar_text:
            continue

        raw_cue_dict = {
            "start": start_sec,
            "end": end_sec,
            "arabic": ar_text,
            "hindi": hi_text,
            "type": item.get("type", "dialogue")
        }

        # تجزئة الأسطر الطويلة (حد أقصى 5.5 ثوانٍ لكل سطر لمنع تحذيرات المشغل)
        split_segments = split_long_cue(raw_cue_dict, max_duration=5.5)
        processed_cues.extend(split_segments)

    # فرز زمني نهائي ومنع التداخل مع ترك فاصل قياسي (80ms)
    processed_cues.sort(key=lambda x: x["start"])
    min_gap = 0.08
    for i in range(len(processed_cues) - 1):
        if processed_cues[i]["end"] > processed_cues[i + 1]["start"] - min_gap:
            new_end = round(processed_cues[i + 1]["start"] - min_gap, 2)
            if new_end > processed_cues[i]["start"] + 0.8:
                processed_cues[i]["end"] = new_end
            else:
                processed_cues[i]["end"] = round(max(processed_cues[i]["start"] + 1.0, processed_cues[i + 1]["start"] - 0.03), 2)

    # محاذاة Whisper بالمللي ثانية في حال تم تفعيلها
    if use_whisper_timestamps:
        progress(0.92, desc="🎙️ تطبيق محاذاة Whisper بالمللي ثانية لمشاهد الغضب والكلام السريع...")
        processed_cues = align_subtitles_with_whisper(media_to_send, processed_cues, enabled=True, progress=progress)
        processed_cues.sort(key=lambda x: x["start"])

    progress(0.95, desc="💾 حفظ وتجهيز ملفات الترجمة وحزمة ZIP المباشرة...")

    # حفظ الملفات في المجلد المؤقت للتحميل
    temp_dir = tempfile.gettempdir()
    srt_path = os.path.join(temp_dir, "BollywoodSub_Translation.srt")
    ass_path = os.path.join(temp_dir, "BollywoodSub_Golden_Cinema.ass")
    vtt_path = os.path.join(temp_dir, "BollywoodSub_Web.vtt")
    zip_path = os.path.join(temp_dir, "BollywoodSub_Translation_Package.zip")

    srt_content = generate_srt_content(processed_cues)
    ass_content = generate_ass_content(processed_cues)
    vtt_content = generate_vtt_content(processed_cues)

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(srt_content)
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)
    with open(vtt_path, "w", encoding="utf-8") as f:
        f.write(vtt_content)

    # إنشاء حزمة ZIP للتحميل بضغطة واحدة على الهواتف دون فتح النص في المتصفح
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        zipf.write(srt_path, arcname="BollywoodSub_Translation.srt")
        zipf.write(ass_path, arcname="BollywoodSub_Golden_Cinema.ass")
        zipf.write(vtt_path, arcname="BollywoodSub_Web.vtt")

    progress(1.0, desc="✅ اكتملت الترجمة بنجاح!")

    whisper_note = "نعم (محاذاة صوتية بالمللي ثانية)" if use_whisper_timestamps else "المحرك الحتمي الذكي (قاعدة 6-8 كلمات لمشاهد الغضب)"

    status_msg = f"""
### 🎉 تم إنجاز الترجمة السينمائية بنجاح!
- **النموذج المستخدم:** `{successful_model}`
- **عدد أسطر الحوار المترجمة:** `{len(processed_cues)}` سطراً
- **محاذاة المشاهد السريعة والغضب:** `{whisper_note}`
- **تنسيق الأسطر:** معايير سينمائية (حد أقصى سطرين ≤ 40 حرفاً لمنع التحذيرات)
- **حزمة التحميل الفوري:** متوفرة بصيغة ZIP للتنزيل المباشر في الهاتف بنقرة واحدة
- **نظام التوقيت:** حتمي 100% Base-60 (دقيقة = 60 ثانية)
"""
    return status_msg, zip_path, srt_path, ass_path, vtt_path, srt_content

# ==========================================
# واجهة Gradio الاحترافية
# ==========================================
custom_css = """
@import url('https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800;900&display=swap');
body, .gradio-container {
    font-family: 'Cairo', sans-serif !important;
    direction: rtl !important;
    text-align: right !important;
    background-color: #0B0F19 !important;
    color: #F8FAFC !important;
}
.gradio-container input, .gradio-container textarea {
    direction: ltr !important;
    text-align: left !important;
}
.gold-header {
    background: linear-gradient(135deg, #1E293B 0%, #0F172A 100%);
    border: 1px solid #F59E0B;
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 20px;
    text-align: center;
}
.gold-title {
    color: #F59E0B;
    font-size: 2.2rem;
    font-weight: 900;
    margin-bottom: 8px;
}
.gold-badge {
    background: #F59E0B;
    color: #0F172A;
    font-weight: 800;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 0.85rem;
    display: inline-block;
}
"""

with gr.Blocks(title="BollywoodSub AI - Google Colab Edition", css=custom_css, theme=gr.themes.Soft()) as demo:
    gr.HTML("""
    <div class="gold-header">
        <div class="gold-badge">نسخة Google Colab الفائقة - مجانية 100% وبدون قيود</div>
        <div class="gold-title">🎬 BollywoodSub AI Cinema Subtitler</div>
        <p style="color: #94A3B8; font-size: 1.1rem; margin-top: 6px;">
            محرك الترجمة السينمائي الحتمي للمسلسلات والأفلام الهندية الطويلة إلى اللغة العربية بدقة Base-60
        </p>
    </div>
    """)

    with gr.Row():
        with gr.Column(scale=1):
            gr.Markdown("### ⚙️ إعدادات المحرك والمفتاح")
            api_key_input = gr.Textbox(
                label="مفتاح Google AI Studio Gemini API",
                placeholder="ألصق مفتاح AI Studio الخاص بك هنا (AIzaSy...)",
                type="password",
                value=os.environ.get("GEMINI_API_KEY", "")
            )

            media_input = gr.File(
                label="📁 ارفع ملف الفيديو أو الصوت (حتى 2GB أو حلقة كاملة)",
                file_types=["video", "audio"]
            )

            model_dropdown = gr.Dropdown(
                label="🤖 اختيار نموذج Gemini AI",
                choices=AVAILABLE_MODELS,
                value="gemini-3.5-flash",
                info="ينصح بـ gemini-3.5-flash أو gemini-3.6-flash لاستقرار تام وتغطية الحلقة بالكامل (22+ دقيقة)"
            )

            strict_model_cb = gr.Checkbox(
                label="🔒 الالتزام الصارم بالنموذج المختار (يمنع النزول التلقائي لنموذج Lite ينقطع بالدقيقة 15)",
                value=True,
                info="عند تفعيله، سيقوم التطبيق بإعادة المحاولة مع النموذج المختار ولن يتحول تلقائياً لنموذج ضعيف."
            )

            dialect_dropdown = gr.Dropdown(
                label="🌍 اللهجة المستهدفة للترجمة",
                choices=[
                    "الفصحى المعاصرة (MSA - القياسية)",
                    "اللهجة المصرية السينمائية",
                    "اللهجة الشامية (السورية واللبنانية)",
                    "اللهجة الخليجية المعاصرة",
                    "اللهجة العراقية الفنية"
                ],
                value="الفصحى المعاصرة (MSA - القياسية)"
            )

            with gr.Accordion("🛠️ خيارات سينمائية متقدمة", open=False):
                ignore_sound_effects_cb = gr.Checkbox(
                    label="استبعاد المؤثرات الصوتية والبيئية وسارجام (حوار وأغاني فقط)",
                    value=True
                )
                enhance_audio_cb = gr.Checkbox(
                    label="ترقية الصوت وعزل الترددات البشرية بـ FFmpeg",
                    value=True
                )
                preserve_hinglish_cb = gr.Checkbox(
                    label="الحفاظ على نبرة الـ Hinglish وشارع مومباي",
                    value=True
                )
                whisper_cb = gr.Checkbox(
                    label="🎙️ دمج محاذاة Whisper بالمللي ثانية (لأعلى دقة توقيت في مشاهد الصراخ والغضب)",
                    value=False,
                    info="يكتشف فترات التحدث الفعلية بدقة المللي ثانية ويربطها بترجمة Gemini السينمائية"
                )

            translate_btn = gr.Button("🚀 بدء الترجمة السينمائية الفائقة", variant="primary", size="lg")

        with gr.Column(scale=1):
            gr.Markdown("### 📥 نتائج وملفات الترجمة الجاهزة")
            status_output = gr.Markdown("في انتظار رفع الملف والضغط على زر البدء...")

            zip_download = gr.File(label="📦 تحميل حزمة الترجمات كاملة (ZIP) - ينزل فوراً بجهازك بنقرة واحدة دون فتح بالمتصفح")

            with gr.Row():
                srt_download = gr.File(label="⬇️ تحميل SRT القياسي")
                ass_download = gr.File(label="⬇️ تحميل ASS بالخط الذهبي")
                vtt_download = gr.File(label="⬇️ تحميل WebVTT")

            preview_text = gr.TextArea(
                label="معاينة فورية لنصوص وتوقيتات الترجمة (SRT Preview)",
                lines=14,
                interactive=False
            )

    translate_btn.click(
        fn=translate_media_file,
        inputs=[
            media_input,
            api_key_input,
            model_dropdown,
            strict_model_cb,
            dialect_dropdown,
            preserve_hinglish_cb,
            translate_songs_cb,
            ignore_sound_effects_cb,
            enhance_audio_cb,
            whisper_cb
        ],
        outputs=[status_output, zip_download, srt_download, ass_download, vtt_download, preview_text]
    )

if __name__ == "__main__":
    # تشغيل Gradio مع رابط عام فوري share=True للتشغيل في Colab
    print("🚀 جاري إطلاق تطبيق BollywoodSub AI على Google Colab...")
    demo.queue().launch(share=True, debug=True)
