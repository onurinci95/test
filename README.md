# N-Panel Top Tabs (Blender eklentisi)

3D Viewport'un sağındaki **N panelde** (Sidebar) dikey sekmeler halinde duran
eklenti kategorilerini (BoxCutter, Blendkit, 3D Print, polygoniq ...) viewport
başlığında **yatay bir buton satırı** olarak gösterir. Her buton tıklanınca o
kategorinin tüm panellerini (alt panelleriyle birlikte) bir popover içinde açar.

Orijinal N panel sekmeleri yerinde kalır; eklenti onlara ikinci, daha okunaklı
bir erişim yolu ekler.

## Kurulum (Blender 4.2+ / 5.x)

1. `n_panel_top_tabs` klasörünü zip'leyin (zip'in içinde klasörün kendisi olsun):
   `zip -r n_panel_top_tabs.zip n_panel_top_tabs`
2. Blender → *Edit > Preferences > Get Extensions* → sağ üstteki ⌄ menü →
   **Install from Disk...** → zip'i seçin.

## Ayarlar (Preferences > Add-ons > N-Panel Top Tabs)

| Ayar | Açıklama |
|---|---|
| Location | Butonların yeri: Tool Settings Header (varsayılan, BlenderKit arama çubuğunun olduğu 2. satır), Viewport Header veya Top Bar |
| Position | **Middle** (varsayılan): satırın ortasındaki boşlukta, BlenderKit arama çubuğu gibi ortalanmış. **Start**: satırın başı (sol). **End**: satırın sonu (sağ) |
| On Click | **Open in Sidebar** (varsayılan): N paneli açıp o sekmeye geçer (açık sekmenin butonu basılı görünür, tekrar tıklayınca N panel kapanır). **Dropdown**: panelleri butonun altında açılan pencerede gösterir |
| Align Right | Butonları başlığın sağına yasla |
| Include Built-in Tabs | Blender'ın kendi Python sekmelerini de ekle |
| Popover Width | Açılan pencerenin genişliği |

### Header'dan düzenleme (sağ tık)

Header'daki bir sekme butonuna **sağ tıklayın**:

- **Rename...**: butonda görünen ismi değiştir
- **Set Icon...**: Blender'ın ikon setinden aranabilir listeyle ikon seç
- **Move Left / Right / to Start / to End**: sırayı değiştir
- **Hide**: sekmeyi satırdan gizle; **Show Hidden Tabs ▸** ile geri getir
- **Show in Preset ▸**: sekmeyi diğer preset'lerde aç/kapat
- **Edit Tabs...**: ayarları aç

*Dropdown* modunda aynı menü, açılan pencerenin başındaki ⌄ butonundadır.

### İkonlar ve buton stili

Her sekmeye ikon atanabilir (sağ tık → Set Icon ya da ayarlardaki listede ikon butonu).
**Button Style**: *Text*, *Icon + Text* (varsayılan) veya *Icon Only* (kompakt;
ikonu olmayan sekmeler isimle gösterilir).

### Preset'ler (sekme setleri)

Her preset kendi sekme listesini (sıra, görünürlük, özel isimler) tutar. Örneğin
"Modeling" setinde BoxCutter ve 3D Print, "Texturing" setinde başka sekmeler.

- Ayarlardaki **Presets** listesinden **+** ile seçili preset'in kopyası olarak yeni
  preset oluşturulur, **−** ile silinir. İsme çift tıklayarak yeniden adlandırılır.
- Bir preset'e **workspace** atanırsa o workspace'e geçince otomatik olarak devreye girer
  (header'da 🖥 ikonuyla gösterilir).
- Birden fazla preset varsa header'daki preset menüsünden tek tıkla geçilir. Elle seçim,
  workspace değiştirilene kadar geçerli kalır.
- 1.3.0 ve öncesindeki sekme listesi ilk açılışta otomatik olarak **Default** preset'ine aktarılır.

### Sekmeleri özelleştirme

Ayarlardaki **Tabs** listesi seçili preset'in sekmelerini gösterir; her sekme bir satırdır:

- 👁 göz ikonu: sekmeyi satırda göster / gizle
- ikon butonu: sekmenin ikonunu seç
- sağdaki metin kutusu: butonda görünecek özel isim (boş = sekmenin kendi adı)
- sağdaki oklar: seçili sekmeyi en üste / yukarı / aşağı / en alta taşı
- 👁 / 🚫: hepsini göster / hepsini gizle
- A→Z: alfabetik sırala
- 🗑: yüklü olmayan (kapatılmış eklentilere ait) sekmeleri listeden sil

Ayarlar Blender tercihleriyle birlikte kaydedilir. Yeni yüklenen eklentilerin sekmeleri
listenin sonuna, görünür olarak eklenir.

Yeni bir eklenti açıp kapattığınızda sekmeler birkaç saniye içinde kendiliğinden
güncellenir; satırın sonundaki ⟳ butonu ile elle de yenileyebilirsiniz.

## Notlar

- **Top Bar** konumunda aktif alan 3D Viewport olmadığı için, `context.space_data`
  gibi viewport verisine dayanan paneller hata verebilir (hata, popover içinde
  etiket olarak gösterilir, Blender çökmez). En iyi sonuç için Tool Settings Header
  veya Viewport Header önerilir.
- Tool Settings satırı görünmüyorsa: viewport'ta *View > Tool Settings*.
- Item / Tool / View'daki Transform gibi C ile tanımlı yerleşik paneller
  Python'dan çizilemediği için bu satıra alınmaz.
