# Telegram Haber Botu

bilgin.pro altyapısındaki haber sitelerini **15 dakikada bir** tarar, takip ettiğin kategorilerdeki (son dakika, siyaset, ekonomi, borsa, spor) yeni haberleri Telegram'a şu biçimde gönderir (temsili örnek):

```
🔴 SON DAKİKA
Merkez Bankası politika faizini yüzde 38'de sabit tuttu

Kurul, enflasyondaki düşüş eğiliminin sürdüğünü belirterek
faizi değiştirmedi. Bir sonraki toplantı Aralık'ta.

📰 Kaynak: Dünya · Ekonomim · CNBC-e
🏷 #Ekonomi  🕒 14:02
```

Bot GitHub'ın sunucularında çalışır, **bilgisayarının açık olması gerekmez** ve ücretsizdir.

## Neler yapıyor?

- 33 aktif kaynağın RSS akışını paralel tarar (`/export/rss` → `/rss` → `/export/newsmap`; bulamazsa ana sayfadan kendisi keşfeder).
- Kategoriyi linkteki bölümden (`/spor/...`, `/ekonomi/...`) ve başlıktaki anahtar kelimelerden belirler. Magazin, galeri, köşe yazısı ve yerel haberleri atlar.
- **Aynı olayı farklı sitelerden tek mesajda birleştirir**, kaynakları yan yana yazar. Son 12 saatte gönderilmiş bir olayı tekrar göndermez.
- Özet: `ANTHROPIC_API_KEY` tanımlıysa özet başlık ve 2-3 cümlelik özeti Claude yazar; değilse sitenin kendi başlığı ve spotu kullanılır.
- Bir turda en fazla 30 tekil mesaj gönderir, fazlasını tek bir "Diğer haberler" listesinde toplar.
- İlk çalıştırmada eski haberleri göndermez; hangi sitelerin çalıştığını raporlar. Bir site ~2 saat taranamazsa seni uyarır.

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
   - Private seçersen aylık ücretsiz dakika kotası (2.000 dk) 15 dakikalık çalışmaya yetmez.
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

Her şey `ayarlar.toml` dosyasında; GitHub'da dosyayı açıp kalem simgesiyle düzenleyebilirsin:

- **Kategori eklemek/çıkarmak:** `takip_edilen = [...]` (ör. medya haberleri için `"medya"` ekle)
- **Site kapatmak/açmak:** ilgili `[[kaynak]]` altında `aktif = false/true`
- **Yeni site eklemek:** yeni bir `[[kaynak]]` bloğu ekle (ad + url yeterli)
- **Anahtar kelime:** ilgili `[kategori.*]` altındaki `kelime` listesine ekle (`bakan*` = bakanı, bakanlık… da yakalar)
- **Mesaj sayısı:** `max_mesaj`

## Bilinmesi gerekenler

- GitHub zamanlanmış görevleri yoğunlukta **5-15 dk gecikebilir**; nadiren bir tur atlanabilir. Bot bunu telafi eder (son 2 saati kontrol eder, haber kaçmaz), sadece bazen 15 yerine 20-30 dk sonra gelir.
- GitHub, 60 gün hiç değişiklik olmayan depolarda zamanlamayı durdurur. İş akışı bunu önlemek için ayda iki kez `canli.txt` dosyasını günceller.
- GitHub'ın sunucuları yurt dışında. Bazı Türk siteleri yurt dışı trafiği engelliyorsa ilk rapordaki "Ulaşılamayan" listesinde görünür.
- Kendi bilgisayarında denemek için: `python bot.py --kuru` (hiçbir şey göndermez, ekrana yazar).
