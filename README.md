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
| Location | Butonların yeri: Viewport Header (varsayılan), Tool Settings Header veya Top Bar |
| Align Right | Butonları başlığın sağına yasla |
| Include Built-in Tabs | Blender'ın kendi Python sekmelerini de ekle |
| Sort Alphabetically | Sekmeleri alfabetik sırala |
| Popover Width | Açılan pencerenin genişliği |
| Hidden Tabs | Gizlenecek sekmeler, virgülle: `Blendkit, polygoniq` |

Yeni bir eklenti açıp kapattığınızda sekmeler birkaç saniye içinde kendiliğinden
güncellenir; satırın sonundaki ⟳ butonu ile elle de yenileyebilirsiniz.

## Notlar

- **Top Bar** konumunda aktif alan 3D Viewport olmadığı için, `context.space_data`
  gibi viewport verisine dayanan paneller hata verebilir (hata, popover içinde
  etiket olarak gösterilir, Blender çökmez). En iyi sonuç için Viewport Header
  önerilir.
- Item / Tool / View'daki Transform gibi C ile tanımlı yerleşik paneller
  Python'dan çizilemediği için bu satıra alınmaz.
