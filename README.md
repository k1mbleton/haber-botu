# Telegram Haber Botu

90 yerli ve yabancı haber kaynağını **30 dakikada bir** tarar (her yarım saatin ~2 dk sonrasında). O 30 dakikalık aralıkta yayımlanan; son dakika, siyaset, dünya siyaseti, finans, borsa, şirketler, teknoloji, spor ve magazin haberlerini Telegram'a gönderir (temsili örnek):

```
🗞 13:30 – 14:00 Ana Haberleri

Merkez Bankası faizi sabit tuttu
(200-300 kelimelik Türkçe özet)
📰 Kaynak: Dünya
🕒 13:42
#Finans
```

Aynı haberi birden fazla site verdiyse tek mesaj gönderilir ve **haberi ilk yayımlayan site** kaynak gösterilir. Yabancı kaynakların haberleri Türkçeye çevrilir.

Bot GitHub'ın sunucularında çalışır, **bilgisayarının açık olması gerekmez** ve ücretsizdir.

## Neler yapıyor?

- 90 aktif kaynağın (69 yerli, 21 yabancı) RSS akışını paralel tarar; RSS adresi verilmemişse kendisi bulur.
- Kategoriyi linkteki bölümden (`/spor/...`, `/ekonomi/...`) ve başlıktaki anahtar kelimelerden belirler. Galeri, video, köşe yazısı ve yerel haberleri atlar.
- Siteler GitHub sunucularını engelliyorsa haberlerini Google Haberler üzerinden alır (özet satırı olmadan gelir).
- **Aynı olayı farklı sitelerden tek mesaja indirir**, ilk yayımlayanı kaynak gösterir. Son 12 saatte gönderilmiş bir olayı tekrar göndermez.
- Özet: `ANTHROPIC_API_KEY` tanımlıysa bot haberin tam metnini çeker ve Claude 200-300 kelimelik Türkçe özet yazar (yabancı haberleri çevirir). Anahtar yoksa haber metninin ilk ~250 kelimesi gönderilir, çeviri yapılmaz. Günlük özet sayısı `claude_gunluk_limit` ile sınırlıdır.
- Bir aralıkta en önemli 30 haber ayrı mesaj olarak gelir (öncelik: son dakika > birden çok sitede çıkan > yerli > erken yayımlanan); sonraki 40 haber "Diğer haberler" listesinde başlık + link olarak gelir.
- Bir site ~2 saat taranamazsa seni uyarır. Kaynak durum raporu için Actions → Run workflow → "rapor" kutusu.

## Kurulum (yaklaşık 15 dakika)

### 1. Telegram botunu oluştur
1. Telegram'da **@BotFather**'a `/newbot` yaz, bir ad ver. Verdiği **token**'ı kaydet.
2. Oluşan botuna kendi hesabından `/start` yaz.
3. Chat ID'ni bul: tarayıcıda `https://api.telegram.org/bot<TOKEN>/getUpdates` adresini aç, `"chat":{"id": 123456789 ...` içindeki sayıyı al.
   (Ya da: `python chat_id_bul.py <TOKEN>`)
   - Kanala göndermek istersen botu kanala **yönetici** olarak ekle; kanal ID'si `-100...` ile başlar. Bu durumda `ayarlar.toml`'da `mesaj_arasi_sn = 3` yap.

### 2. GitHub deposunu oluştur
1. github.com'da ücretsiz hesap aç → **New repository** → ad: `haber-botu` → **Public** seç → Create.
   - Public olması GitHub Actions'ı sınırsız ücretsiz yapar. Kodun görünür ama token'ların görünmez (gizli değişken olarak saklanır).
   - Private seçersen aylık ücretsiz dakika kotası (2.000 dk) 30 dakikalık çalışmaya yetmeyebilir.
2. **Add file → Upload files** ile bu klasördeki her şeyi (`.github` klasörü dahil) sürükleyip bırak → Commit.
   - `.github` klasörü görünmüyorsa: Mac'te Finder'da `Cmd+Shift+.`, Windows'ta Görünüm → Gizli öğeler.

### 3. Gizli değişkenleri ekle
Depoda **Settings → Secrets and variables → Actions → New repository secret**:

| Ad | Değer |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather'ın verdiği token |
| `TELEGRAM_CHAT_ID` | *(isteğe bağlı)* Boş bırakılırsa bot, kendisine /start yazan sohbeti kendisi bulur |
| `ANTHROPIC_API_KEY` | *(isteğe bağlı)* console.anthropic.com'dan API anahtarı |

### 4. Başlat
**Actions** sekmesi → (istenirse "I understand… enable") → **Haber Botu** → **Run workflow** → `rapor` kutusunu işaretle → Run.

Bir dakika içinde Telegram'a kaynak durum raporu ve "bot kuruldu" mesajı gelir. Sonraki turlardan itibaren yeni haberler akmaya başlar.

## Ayarlar

Ayarlar `ayarlar.toml`, haber kaynakları `kaynaklar.toml` dosyasında; GitHub'da dosyayı açıp kalem simgesiyle düzenleyebilirsin:

- **Kategori eklemek/çıkarmak:** `takip_edilen = [...]` (ör. radyo/TV sektör haberleri için `"medya"` ekle)
- **Site kapatmak/açmak:** `kaynaklar.toml`'da ilgili satırda `aktif = false/true`
- **Yeni site eklemek:** `kaynaklar.toml`'a bir satır ekle (ad + url yeterli; yabancıysa `dil = "en"`)
- **Anahtar kelime:** ilgili `[kategori.*]` altındaki `kelime` listesine ekle (`bakan*` = bakanı, bakanlık… da yakalar)
- **Mesaj sayısı:** `max_mesaj`, `max_liste`; **özet uzunluğu:** `ozet_kelime_min/max`; **aralık:** `aralik_dk` (cron satırıyla birlikte)

## Bilinmesi gerekenler

- GitHub zamanlanmış görevleri yoğunlukta **5-15 dk gecikebilir**; nadiren bir tur atlanabilir. Bot bunu telafi eder (son 2 saati kontrol eder, haber kaçmaz), sadece bazen birkaç dakika geç gelir.
- GitHub, 60 gün hiç değişiklik olmayan depolarda zamanlamayı durdurur. İş akışı bunu önlemek için ayda iki kez `canli.txt` dosyasını günceller.
- GitHub'ın sunucuları yurt dışında. Bazı Türk siteleri yurt dışı trafiği engelliyorsa ilk rapordaki "Ulaşılamayan" listesinde görünür.
- Kendi bilgisayarında denemek için: `python bot.py --kuru` (hiçbir şey göndermez, ekrana yazar).
