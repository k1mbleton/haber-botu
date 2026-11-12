#!/usr/bin/env python3
"""
Telegram Haber Botu
-------------------
ayarlar.toml'daki haber sitelerinin RSS akışlarını tarar, takip edilen
kategorilerdeki yeni haberleri bulur, aynı olayı farklı sitelerden tek
mesaja indirir ve haberi ilk yayımlayan siteyi kaynak göstererek
Telegram'a gönderir.

Her çalıştırmada tamamlanmış son 15 dakikalık aralığı işler
(ör. 13:32'de çalışırsa 13:15-13:30 arasında yayımlanan haberler):

    🗞 13:15 – 13:30 Ana Haberleri
    ────────────
    Özet Başlığı
    Özet (en fazla 100 karakter)
    📰 Kaynak: İlk yayımlayan site
    🕒 13:22
    #Konu

Yalnızca Python standart kütüphanesini kullanır (kurulum gerektirmez).

Ortam değişkenleri:
    TELEGRAM_BOT_TOKEN   BotFather'dan alınan token        (zorunlu)
    TELEGRAM_CHAT_ID     Mesajın gideceği sohbet/kanal ID   (isteğe bağlı; yoksa /start yazan sohbet)
    ANTHROPIC_API_KEY    Claude ile özet için               (isteğe bağlı)

Kullanım:
    python bot.py            # normal tur
    python bot.py --kuru     # hiçbir şey göndermeden ekrana yazar, durumu kaydetmez
    python bot.py --rapor    # kaynakların çalışıp çalışmadığını Telegram'a raporlar
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

KOK = Path(__file__).resolve().parent
AYAR_DOSYASI = KOK / "ayarlar.toml"
DURUM_DOSYASI = KOK / "data" / "durum.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
VARSAYILAN_YOLLAR = ["/export/rss", "/rss", "/export/newsmap"]
GORULEN_SAKLAMA_GUN = 4
GONDERILEN_SAKLAMA_SAAT = 12
ARALIK = timedelta(minutes=15)
HATA_UYARI_ESIGI = 8          # art arda 8 tur (~2 saat) hata -> tek seferlik uyarı
UTC = timezone.utc
TR = timezone(timedelta(hours=3))


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
#  Metin yardımcıları
# --------------------------------------------------------------------------- #
def tr_kucuk(s: str) -> str:
    return s.replace("I", "ı").replace("İ", "i").lower()


def temizle(s: str | None) -> str:
    """HTML etiketlerini ve (çift) kaçış karakterlerini temizler."""
    if not s:
        return ""
    for _ in range(2):
        s = html.unescape(s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def kisalt(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    kes = s[:n].rsplit(" ", 1)[0]
    return kes.rstrip(",;:-– ") + "…"


def ilk_cumleler(s: str, adet: int = 2, n: int = 400) -> str:
    cumleler = re.split(r"(?<=[.!?])\s+", s)
    return kisalt(" ".join(cumleler[:adet]).strip(), n)


DURAK = set("""ve ile bir bu şu o da de mi mı mu mü ne için gibi daha en çok son dakika
haber haberi haberleri flaş açıklama açıklaması oldu etti yaptı geldi işte video foto galeri
canlı kim neden nasıl ilk yeni sonra önce olan olarak var yok her tüm""".split())


def jetonlar(baslik: str) -> set[str]:
    kelimeler = re.findall(r"\w+", tr_kucuk(baslik))
    # 6 harfe kesmek Türkçe ekler için kaba bir kök bulma görevi görür
    return {k[:6] for k in kelimeler if len(k) > 2 and k not in DURAK}


def benzerlik(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def link_anahtari(link: str) -> str:
    p = urllib.parse.urlsplit(link.strip())
    norm = f"{p.netloc.lower().removeprefix('www.')}{p.path.rstrip('/')}"
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


def tarih_coz(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=TR)
    return d.astimezone(UTC)


# --------------------------------------------------------------------------- #
#  Ağ
# --------------------------------------------------------------------------- #
def getir(url: str, zaman_asimi: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.5",
    })
    with urllib.request.urlopen(req, timeout=zaman_asimi) as r:
        return r.read()


# --------------------------------------------------------------------------- #
#  RSS / Atom / Sitemap ayrıştırma
# --------------------------------------------------------------------------- #
@dataclass
class Haber:
    kaynak: str
    baslik: str
    link: str
    ozet: str = ""
    tarih: datetime | None = None
    kategoriler: list[str] = field(default_factory=list)
    # sınıflandırma sonrası
    kategori: str = ""
    son_dakika: bool = False
    dolayli: bool = False        # Google Haberler üzerinden geldiyse

    @property
    def anahtar(self) -> str:
        return link_anahtari(self.link)


def _ad(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _cocuk(el, ad):
    for c in el:
        if _ad(c.tag) == ad:
            return c
    return None


def _metin(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _xml_kok(veri: bytes):
    try:
        return ET.fromstring(veri)
    except ET.ParseError:
        metin = veri.decode("utf-8", errors="replace")
        metin = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", metin)
        metin = re.sub(r"&(?!#?\w+;)", "&amp;", metin)
        metin = re.sub(r"^\s*<\?xml[^>]*\?>", "", metin)
        return ET.fromstring(metin)


def akisi_ayristir(veri: bytes, kaynak: str) -> list[Haber]:
    kok = _xml_kok(veri)
    haberler: list[Haber] = []
    for el in kok.iter():
        ad = _ad(el.tag)
        if ad == "item":                                   # RSS 2.0
            link = _metin(_cocuk(el, "link"))
            if not link:
                guid = _metin(_cocuk(el, "guid"))
                link = guid if guid.startswith("http") else ""
            haberler.append(Haber(
                kaynak=kaynak,
                baslik=temizle(_metin(_cocuk(el, "title"))),
                link=link,
                ozet=temizle(_metin(_cocuk(el, "description"))),
                tarih=tarih_coz(_metin(_cocuk(el, "pubdate")) or _metin(_cocuk(el, "date"))),
                kategoriler=[temizle(_metin(c)) for c in el if _ad(c.tag) == "category"],
            ))
        elif ad == "entry":                                # Atom
            link_el = next((c for c in el if _ad(c.tag) == "link"
                            and c.get("rel", "alternate") == "alternate"), None)
            haberler.append(Haber(
                kaynak=kaynak,
                baslik=temizle(_metin(_cocuk(el, "title"))),
                link=(link_el.get("href") if link_el is not None else ""),
                ozet=temizle(_metin(_cocuk(el, "summary")) or _metin(_cocuk(el, "content"))),
                tarih=tarih_coz(_metin(_cocuk(el, "published")) or _metin(_cocuk(el, "updated"))),
            ))
        elif ad == "url" and _cocuk(el, "loc") is not None:  # Google News sitemap
            news = _cocuk(el, "news")
            baslik = _metin(_cocuk(news, "title")) if news is not None else ""
            tarih = _metin(_cocuk(news, "publication_date")) if news is not None else ""
            haberler.append(Haber(
                kaynak=kaynak,
                baslik=temizle(baslik),
                link=_metin(_cocuk(el, "loc")),
                tarih=tarih_coz(tarih or _metin(_cocuk(el, "lastmod"))),
            ))
    return [h for h in haberler if h.link and h.baslik]


def html_akis_bul(ana_sayfa: str) -> list[str]:
    """Ana sayfadaki <link rel="alternate" type="application/rss+xml"> etiketlerini bulur."""
    try:
        s = getir(ana_sayfa).decode("utf-8", errors="replace")
    except Exception:
        return []
    bulunan = []
    for m in re.finditer(r"<link[^>]+>", s, re.I):
        etiket = m.group(0)
        if re.search(r"application/(rss|atom)\+xml", etiket, re.I):
            href = re.search(r'href=["\']([^"\']+)', etiket)
            if href:
                bulunan.append(urllib.parse.urljoin(ana_sayfa, html.unescape(href.group(1))))
    return bulunan


def kaynagi_tara(kaynak: dict, durum: dict) -> tuple[list[Haber], str | None, str | None]:
    """(haberler, çalışan_akış_url, hata) döner."""
    ad, kok = kaynak["ad"], kaynak["url"].rstrip("/")
    kd = durum.setdefault("kaynaklar", {}).get(ad, {})
    adaylar: list[str] = []
    if kd.get("akis") and "news.google.com" not in kd["akis"]:
        adaylar.append(kd["akis"])
    adaylar += kaynak.get("rss", []) or [kok + y for y in VARSAYILAN_YOLLAR]
    adaylar = list(dict.fromkeys(adaylar))
    son_hata = None
    for deneme in range(2):
        for url in adaylar:
            try:
                haberler = akisi_ayristir(getir(url), ad)
                if haberler:
                    return haberler, url, None
                son_hata = f"{url}: boş akış"
            except urllib.error.HTTPError as e:
                son_hata = f"{url}: HTTP {e.code}"
            except Exception as e:  # zaman aşımı, bozuk XML vb.
                son_hata = f"{url}: {type(e).__name__}: {e}"[:200]
        if deneme == 0:   # ikinci deneme: ana sayfadan akış keşfi
            yeni = [u for u in html_akis_bul(kok + "/") if u not in adaylar]
            if not yeni:
                break
            adaylar = yeni
    # Son çare: site yurt dışı/veri merkezi trafiğini engelliyorsa Google Haberler üzerinden
    gurl = google_haberler_url(kok)
    try:
        haberler = akisi_ayristir(getir(gurl), ad)
        for h in haberler:
            h.dolayli = True
            h.baslik = re.sub(r"\s+-\s+[^-]+$", "", h.baslik)   # " - Kaynak Adı" ekini at
            h.ozet = ""                                          # Google'ın özeti başlığın tekrarı
        if haberler:
            return haberler, gurl, None
    except Exception as e:
        son_hata = f"{son_hata} | Google Haberler: {type(e).__name__}"
    return [], None, son_hata


def google_haberler_url(kok: str) -> str:
    alan = urllib.parse.urlsplit(kok).netloc.removeprefix("www.")
    q = urllib.parse.quote(f"site:{alan} when:1d")
    return f"https://news.google.com/rss/search?q={q}&hl=tr&gl=TR&ceid=TR:tr"


def meta_aciklama(link: str) -> str:
    """Spotu olmayan haberler için sayfadaki og:description'ı alır."""
    try:
        s = getir(link, 15).decode("utf-8", errors="replace")[:300_000]
    except Exception:
        return ""
    for desen in (r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)',
                  r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description',
                  r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)'):
        m = re.search(desen, s, re.I)
        if m:
            return temizle(m.group(1))
    return ""


# --------------------------------------------------------------------------- #
#  Sınıflandırma
# --------------------------------------------------------------------------- #
class Siniflandirici:
    def __init__(self, ayar: dict):
        g = ayar["genel"]
        self.takip = set(g["takip_edilen"])
        self.haric = {tr_kucuk(x) for x in g.get("haric_url", [])}
        self.oncelik = g.get("oncelik", [])
        self.etiket = {k: v.get("etiket", k) for k, v in ayar["kategori"].items()}
        self.url_harita: dict[str, str] = {}
        self.kelime_desen: dict[str, re.Pattern] = {}
        for ad, k in ayar["kategori"].items():
            for u in k.get("url", []):
                self.url_harita.setdefault(tr_kucuk(u), ad)
            parcalar = []
            for kel in k.get("kelime", []):
                kel = tr_kucuk(kel.strip())
                if kel.endswith("*"):
                    parcalar.append(r"(?<!\w)" + re.escape(kel[:-1]))
                else:
                    parcalar.append(r"(?<!\w)" + re.escape(kel) + r"(?:['’]\w+)?(?!\w)")
            if parcalar:
                self.kelime_desen[ad] = re.compile("|".join(parcalar))

    @staticmethod
    def _bolumler(link: str) -> list[str]:
        """Linkteki bölüm adları: /spor/futbol/haber-adi-123 -> ['spor', 'futbol']."""
        parca = [tr_kucuk(p) for p in urllib.parse.urlsplit(link).path.split("/") if p]
        return parca[:-1][:2]   # son parça haberin kendisi

    def sinifla(self, h: Haber) -> bool:
        """Haberi sınıflar; gönderilecekse True döner."""
        bolumler = self._bolumler(h.link)
        if any(p in self.haric for p in bolumler):
            return False
        url_katlar = [self.url_harita[p] for p in bolumler if p in self.url_harita]
        for c in h.kategoriler:      # RSS <category> varsa ona da bak
            k = self.url_harita.get(tr_kucuk(c).replace(" ", "-"))
            if k:
                url_katlar.append(k)
        sd_url = "son_dakika" in url_katlar
        url_kat = next(iter(self._sirala([k for k in url_katlar if k != "son_dakika"])), "")

        metin = tr_kucuk(h.baslik)
        kelime_kat = [k for k, d in self.kelime_desen.items() if d.search(metin)]

        h.son_dakika = sd_url or "son_dakika" in kelime_kat
        adaylar = [k for k in kelime_kat if k != "son_dakika"]
        if url_kat:
            kat = url_kat
            if kat == "ekonomi" and "borsa" in adaylar:
                kat = "borsa"
            if kat not in self.takip:     # genel bölüm (gündem vb.) -> başlığa bak
                takipte = [k for k in self._sirala(adaylar) if k in self.takip]
                kat = takipte[0] if takipte else kat
        else:
            sirali = self._sirala(adaylar)
            takipte = [k for k in sirali if k in self.takip]
            kat = takipte[0] if takipte else (sirali[0] if sirali else "gundem")
        h.kategori = kat
        return kat in self.takip or (h.son_dakika and "son_dakika" in self.takip)

    def _sirala(self, kats: list[str]) -> list[str]:
        sira = {k: i for i, k in enumerate(self.oncelik)}
        return sorted(kats, key=lambda k: sira.get(k, 99))


# --------------------------------------------------------------------------- #
#  Gruplama (aynı olay farklı siteler)
# --------------------------------------------------------------------------- #
@dataclass
class Grup:
    haberler: list[Haber]
    jeton: set[str]
    ozet_baslik: str = ""
    ozet: str = ""

    @property
    def ana(self) -> Haber:
        return self.haberler[0]

    @property
    def son_dakika(self) -> bool:
        return any(h.son_dakika for h in self.haberler)

    @property
    def tarih(self) -> datetime:
        return min((h.tarih for h in self.haberler if h.tarih), default=datetime.now(UTC))


def grupla(haberler: list[Haber], esik: float) -> list[Grup]:
    gruplar: list[Grup] = []
    for h in sorted(haberler, key=lambda x: x.tarih or datetime.now(UTC)):
        j = jetonlar(h.baslik)
        en_iyi, skor = None, 0.0
        for g in gruplar:
            s = benzerlik(j, g.jeton)
            if s > skor:
                en_iyi, skor = g, s
        if en_iyi and skor >= esik:
            if h.kaynak not in {x.kaynak for x in en_iyi.haberler}:
                en_iyi.haberler.append(h)
            en_iyi.jeton |= j
        else:
            gruplar.append(Grup([h], j))
    return gruplar


# --------------------------------------------------------------------------- #
#  Özetleme
# --------------------------------------------------------------------------- #
SIRALI_ONEK = re.compile(r"^\s*(son dakika|flaş|sıcak gelişme)\s*[:|!\-–—]*\s*", re.I)


def ozet_kisalt(s: str, n: int) -> str:
    """İlk cümle sığıyorsa onu, sığmıyorsa kelime sınırında kesilmiş halini döner."""
    s = s.strip()
    # "12. hafta" gibi sayılarda bölmemek için noktadan önce harf, sonra büyük harf aranır
    ilk = re.split(r"(?<=[^\W\d][.!?])\s+(?=[A-ZÇĞİÖŞÜ\"“'])", s)[0]
    return ilk if 30 <= len(ilk) <= n else kisalt(s, n - 1)   # "…" dahil n karakter


def basit_ozet(g: Grup, n: int = 100):
    h = g.ana
    g.ozet_baslik = SIRALI_ONEK.sub("", h.baslik) or h.baslik
    spot = next((x.ozet for x in g.haberler if x.ozet and x.ozet != x.baslik), "")
    g.ozet = ozet_kisalt(spot, n) if spot else ""


def claude_ozetle(gruplar: list[Grup], model: str, anahtar: str, n: int = 100) -> int:
    """Grupları 15'erli paketler halinde Claude'a özetletir. Başarılı grup sayısını döner."""
    basarili = 0
    for i in range(0, len(gruplar), 15):
        paket = gruplar[i:i + 15]
        girdi = [{
            "id": k,
            "haberler": [{"kaynak": h.kaynak, "baslik": h.baslik, "spot": kisalt(h.ozet, 700)}
                         for h in g.haberler],
        } for k, g in enumerate(paket)]
        istem = (
            "Aşağıda Türk haber sitelerinden gelen haber grupları var. Her grup aynı olayı "
            "anlatan bir veya birkaç haberden oluşuyor. Her grup için Türkçe:\n"
            "- ozet_baslik: en fazla 12 kelime, tarafsız, bilgilendirici; tıklama tuzağı, "
            "büyük harf ve 'SON DAKİKA' gibi ekler olmadan.\n"
            f"- ozet: tek cümle, EN FAZLA {n} karakter; yalnızca verilen başlık ve spotlardaki "
            "bilgilere dayan, başlığı tekrarlama, yorum veya tahmin ekleme.\n"
            "Yalnızca JSON dizisi döndür, başka hiçbir şey yazma: "
            '[{"id": 0, "ozet_baslik": "...", "ozet": "..."}]\n\n'
            + json.dumps(girdi, ensure_ascii=False)
        )
        govde = json.dumps({
            "model": model,
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": istem}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=govde, method="POST",
            headers={"x-api-key": anahtar, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                yanit = json.loads(r.read())
            metin = "".join(b.get("text", "") for b in yanit.get("content", []))
            m = re.search(r"\[.*\]", metin, re.S)
            for o in json.loads(m.group(0)) if m else []:
                k = o.get("id")
                if isinstance(k, int) and 0 <= k < len(paket) and o.get("ozet_baslik"):
                    paket[k].ozet_baslik = o["ozet_baslik"].strip()
                    paket[k].ozet = ozet_kisalt(o.get("ozet") or "", n)
                    basarili += 1
        except Exception as e:
            log(f"[claude] özetleme hatası, yedek yöntem kullanılacak: {e}")
    return basarili


# --------------------------------------------------------------------------- #
#  Telegram
# --------------------------------------------------------------------------- #
def e(s: str) -> str:
    return html.escape(s or "", quote=False)


def mesaj_olustur(g: Grup, etiket: str) -> str:
    h = g.ana                       # grubun en erken yayımlanan haberi
    satirlar = [f"<b>{e(g.ozet_baslik)}</b>"]
    if g.ozet:
        satirlar.append(e(g.ozet))
    satirlar.append(f'📰 Kaynak: <a href="{html.escape(h.link, quote=True)}">{e(h.kaynak)}</a>')
    zaman = (h.tarih or g.tarih).astimezone(TR).strftime("%H:%M")
    satirlar.append(f"🕒 {zaman}")
    etiketler = [f"#{etiket.replace(' ', '')}"]
    if g.son_dakika:
        etiketler.append("#SonDakika")
    satirlar.append(" ".join(etiketler))
    return "\n".join(satirlar)


def baslik_mesaji(bas: datetime, son: datetime) -> str:
    b, s_ = bas.astimezone(TR), son.astimezone(TR)
    return f"🗞 <b>{b:%H:%M} – {s_:%H:%M} Ana Haberleri</b>\n<i>{b:%d.%m.%Y}</i>"


class Telegram:
    def __init__(self, token: str | None, chat_id: str | None, kuru: bool, onizleme: bool):
        self.token, self.chat_id, self.kuru, self.onizleme = token, chat_id, kuru, onizleme

    def gonder(self, metin: str, onizleme: bool | None = None) -> bool:
        if self.kuru:
            print("\n" + "─" * 60 + "\n" + metin)
            return True
        onizleme = self.onizleme if onizleme is None else onizleme
        veri = json.dumps({
            "chat_id": self.chat_id, "text": metin[:4096], "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": not onizleme},
        }).encode()
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        for deneme in range(4):
            req = urllib.request.Request(url, data=veri, headers={"content-type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read()).get("ok", False)
            except urllib.error.HTTPError as err:
                govde = err.read().decode(errors="replace")
                if err.code == 429:
                    try:
                        bekle = json.loads(govde)["parameters"]["retry_after"]
                    except Exception:
                        bekle = 5
                    log(f"[telegram] hız sınırı, {bekle} sn bekleniyor")
                    time.sleep(bekle + 1)
                    continue
                log(f"[telegram] HTTP {err.code}: {govde[:300]}")
                return False
            except Exception as ex:
                log(f"[telegram] hata: {ex}")
                time.sleep(3)
        return False


def sohbet_bul(token: str) -> str | None:
    """TELEGRAM_CHAT_ID verilmemişse: bota en son /start yazan özel sohbetin ID'sini bulur."""
    try:
        with urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/getUpdates", timeout=30) as r:
            guncellemeler = json.loads(r.read()).get("result", [])
    except Exception as ex:
        log(f"[telegram] getUpdates hatası: {ex}")
        return None
    for u in reversed(guncellemeler):
        m = u.get("message") or {}
        if m.get("chat", {}).get("type") == "private" and (m.get("text") or "").startswith("/start"):
            return str(m["chat"]["id"])
    return None


def parcala(satirlar: list[str], baslik: str, sinir: int = 3800) -> list[str]:
    mesajlar, cur = [], baslik
    for s in satirlar:
        if len(cur) + len(s) + 1 > sinir:
            mesajlar.append(cur)
            cur = baslik + " (devam)"
        cur += "\n" + s
    mesajlar.append(cur)
    return mesajlar


# --------------------------------------------------------------------------- #
#  Durum
# --------------------------------------------------------------------------- #
def durum_yukle() -> dict:
    try:
        return json.loads(DURUM_DOSYASI.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def durum_kaydet(d: dict):
    DURUM_DOSYASI.parent.mkdir(parents=True, exist_ok=True)
    tmp = DURUM_DOSYASI.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(DURUM_DOSYASI)


def durum_buda(d: dict, simdi: datetime):
    sinir = (simdi - timedelta(days=GORULEN_SAKLAMA_GUN)).timestamp()
    d["gorulen"] = {k: v for k, v in d.get("gorulen", {}).items() if v >= sinir}
    d["ilk"] = {k: v for k, v in d.get("ilk", {}).items() if v >= sinir and k not in d["gorulen"]}
    sinir2 = (simdi - timedelta(hours=GONDERILEN_SAKLAMA_SAAT)).timestamp()
    d["gonderilen"] = [x for x in d.get("gonderilen", []) if x["t"] >= sinir2]


# --------------------------------------------------------------------------- #
#  Ana akış
# --------------------------------------------------------------------------- #
def rapor_metni(sonuclar: dict, aktif: list[dict]) -> str:
    akis = {k["ad"]: sonuclar[k["ad"]][1] or "" for k in aktif}
    dogrudan = [a for a, u in akis.items() if u and "news.google.com" not in u]
    dolayli = [a for a, u in akis.items() if "news.google.com" in u]
    bozuk = [(k["ad"], sonuclar[k["ad"]][2]) for k in aktif if not akis[k["ad"]]]
    satir = [f"📊 <b>Kaynak durumu</b>: {len(dogrudan) + len(dolayli)}/{len(aktif)} site taranıyor"]
    if dogrudan:
        satir.append(f"\n✅ Doğrudan ({len(dogrudan)}): " + ", ".join(e(x) for x in dogrudan))
    if dolayli:
        satir.append(f"\n↪️ Google Haberler üzerinden ({len(dolayli)}; site doğrudan erişimi engelliyor): "
                     + ", ".join(e(x) for x in dolayli))
    if bozuk:
        satir.append("\n❌ Ulaşılamayan:")
        satir += [f"• {e(a)} — <i>{e((h or '')[:120])}</i>" for a, h in bozuk]
    return "\n".join(satir)


def calistir(kuru: bool = False, rapor: bool = False) -> int:
    ayar = tomllib.loads(AYAR_DOSYASI.read_text("utf-8"))
    g = ayar["genel"]
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    durum = durum_yukle()
    if not kuru:
        if not token:
            log("HATA: TELEGRAM_BOT_TOKEN tanımlanmalı (veya --kuru kullanın).")
            return 2
        if not chat_id:
            chat_id = durum.get("chat_id") or sohbet_bul(token)
            if not chat_id:
                log("HATA: Sohbet bulunamadı. Telegram'da bota /start yazıp tekrar çalıştırın "
                    "ya da TELEGRAM_CHAT_ID gizli değişkenini ekleyin.")
                return 2
            if durum.get("chat_id") != chat_id:
                durum["chat_id"] = chat_id
                Telegram(token, chat_id, False, False).gonder(
                    f"🔗 Bu sohbet bağlandı. Sohbet ID: <code>{chat_id}</code>\n"
                    "(Kalıcı olması için GitHub'da TELEGRAM_CHAT_ID olarak da eklenebilir.)")
    tg = Telegram(token, chat_id, kuru, g.get("link_onizleme", True))
    simdi = datetime.now(UTC)
    ilk_calisma = not durum.get("gorulen")
    durum.setdefault("gorulen", {})
    durum.setdefault("kaynaklar", {})

    # 1) Tara
    aktif = [k for k in ayar["kaynak"] if k.get("aktif", True)]
    with ThreadPoolExecutor(max_workers=12) as havuz:
        sonuc_listesi = list(havuz.map(lambda k: kaynagi_tara(k, durum), aktif))
    sonuclar = {k["ad"]: s for k, s in zip(aktif, sonuc_listesi)}

    tum: list[Haber] = []
    for k in aktif:
        haberler, akis, hata = sonuclar[k["ad"]]
        kd = durum["kaynaklar"].setdefault(k["ad"], {})
        if akis:
            if kd.get("hata", 0) >= HATA_UYARI_ESIGI:
                tg.gonder(f"✅ {e(k['ad'])} yeniden çalışıyor.", False)
            kd.update(akis=akis, hata=0, son_basari=simdi.isoformat(timespec="minutes"))
            tum += haberler
        else:
            kd["hata"] = kd.get("hata", 0) + 1
            kd["son_hata"] = hata
            if kd["hata"] == HATA_UYARI_ESIGI and not ilk_calisma:
                tg.gonder(f"⚠️ {e(k['ad'])} yaklaşık 2 saattir taranamıyor:\n<i>{e(hata or '')}</i>", False)
        log(f"[tara] {k['ad']:<16} {len(haberler):>3} haber  {akis or 'HATA: ' + str(hata)}")

    if rapor:
        tg.gonder(rapor_metni(sonuclar, aktif), False)

    # 2) Hangi 15 dakikalık aralık(lar) işlenecek?
    E = ceyrek_taban(simdi)                      # son tamamlanmış aralığın bitişi
    son_isaret = durum.get("son_pencere")
    taze = not son_isaret
    if taze:
        baslangic = E - ARALIK
    else:
        geriye = E - ARALIK * int(g.get("max_geriye_aralik", 4))
        baslangic = max(datetime.fromisoformat(son_isaret), geriye)
    if baslangic >= E:
        log(f"[bitti] {E.astimezone(TR):%H:%M} aralığı zaten işlenmiş")
        return kaydet_ve_cik(durum, simdi, kuru)
    pencereler = []
    t = baslangic
    while t < E:
        pencereler.append((t, t + ARALIK, []))
        t += ARALIK

    # 3) Haberleri aralıklara dağıt
    gorulen, ilk = durum["gorulen"], durum.setdefault("ilk", {})
    gec_sinir = timedelta(minutes=g["max_haber_yasi_dk"])
    tekil: dict[str, Haber] = {}
    for h in tum:
        tekil.setdefault(h.anahtar, h)
    ertelenen = gec = 0
    for anahtar, h in tekil.items():
        if anahtar in gorulen:
            continue
        if not h.tarih:                          # tarihsiz haber: ilk görüldüğü an
            ilk.setdefault(anahtar, simdi.timestamp())
            h.tarih = datetime.fromtimestamp(ilk[anahtar], UTC)
        if h.tarih >= E:                         # henüz kapanmamış aralık -> sonraki tura
            ertelenen += 1
            continue
        gorulen[anahtar] = simdi.timestamp()
        if h.tarih >= baslangic:
            for bas, son, liste in pencereler:
                if bas <= h.tarih < son:
                    liste.append(h)
                    break
        elif not taze and simdi - h.tarih <= gec_sinir:
            pencereler[0][2].append(h)           # geç yayına giren (ör. Google) haber
            gec += 1
    log(f"[aralık] {len(pencereler)} aralık, {ertelenen} haber sonraki tura ertelendi, {gec} geç gelen")

    # 4) Her aralık için: sınıfla, birleştir, özetle, gönder
    sinif = Siniflandirici(ayar)
    esik = g.get("benzerlik_esigi", 0.5)
    n_ozet = int(g.get("ozet_max_karakter", 100))
    api = os.environ.get("ANTHROPIC_API_KEY") if g.get("claude_kullan", True) else None
    bekle = 0 if kuru else g.get("mesaj_arasi_sn", 1.5)
    for bas, son, liste in pencereler:
        secilen = [h for h in liste if sinif.sinifla(h)]
        gruplar = grupla(secilen, esik)
        onceki = [set(x["j"]) for x in durum.get("gonderilen", [])]
        gruplar = [gr for gr in gruplar if not any(benzerlik(gr.jeton, o) >= esik for o in onceki)]
        gruplar.sort(key=lambda gr: gr.tarih)
        log(f"[{bas.astimezone(TR):%H:%M}-{son.astimezone(TR):%H:%M}] {len(liste)} yeni, "
            f"{len(secilen)} takip edilen, {len(gruplar)} mesaj")
        if not gruplar and not g.get("bos_araligi_bildir", False):
            continue
        tg.gonder(baslik_mesaji(bas, son), False)
        time.sleep(bekle)
        if not gruplar:
            tg.gonder("<i>Bu aralıkta takip edilen konularda yeni haber yok.</i>", False)
            continue
        tekli, fazla = gruplar[: g["max_mesaj"]], gruplar[g["max_mesaj"]:]
        for gr in tekli:
            basit_ozet(gr, n_ozet)
            if not gr.ozet and not gr.ana.dolayli:
                gr.ana.ozet = meta_aciklama(gr.ana.link)
                basit_ozet(gr, n_ozet)
        if api:
            k = claude_ozetle(tekli, g.get("claude_model", "claude-haiku-5-5"), api, n_ozet)
            log(f"[claude] {k}/{len(tekli)} grup Claude ile özetlendi")
        for gr in tekli:
            etiket = sinif.etiket.get(gr.ana.kategori, gr.ana.kategori)
            tg.gonder(mesaj_olustur(gr, etiket))
            durum.setdefault("gonderilen", []).append({"t": simdi.timestamp(), "j": sorted(gr.jeton)})
            time.sleep(bekle)
        if fazla:
            satirlar = [f'• <a href="{html.escape(gr.ana.link, quote=True)}">{e(gr.ana.baslik)}</a> '
                        f"<i>({e(gr.ana.kaynak)}, {gr.tarih.astimezone(TR):%H:%M})</i>" for gr in fazla]
            for m in parcala(satirlar, f"🗂 <b>Diğer haberler</b> ({len(fazla)})"):
                tg.gonder(m, False)
                time.sleep(bekle)
            for gr in fazla:
                durum.setdefault("gonderilen", []).append({"t": simdi.timestamp(), "j": sorted(gr.jeton)})

    durum["son_pencere"] = E.isoformat()
    return kaydet_ve_cik(durum, simdi, kuru)


def ceyrek_taban(t: datetime) -> datetime:
    return t.replace(minute=t.minute - t.minute % 15, second=0, microsecond=0)


def kaydet_ve_cik(durum: dict, simdi: datetime, kuru: bool) -> int:
    if not kuru:
        durum_buda(durum, simdi)
        durum_kaydet(durum)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Telegram haber botu")
    p.add_argument("--kuru", action="store_true", help="göndermeden ekrana yaz, durumu kaydetme")
    p.add_argument("--rapor", action="store_true", help="kaynak durum raporunu gönder")
    a = p.parse_args()
    sys.exit(calistir(kuru=a.kuru, rapor=a.rapor))
