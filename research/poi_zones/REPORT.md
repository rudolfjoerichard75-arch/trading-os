# Ujian zone POI berterusan — OB dan FVG M15

**Keputusan: belum ada bukti peningkatan prestasi. Jangan aktifkan untuk live berdasarkan sampel ini.**

Dataset yang sama dengan kajian POI terdahulu: 1,028 candle M5, 18 Oktober 15:15 hingga 22 Oktober 04:50 MYT. Bahagian awal ialah warm-up. Data ialah snapshot tersimpan dengan revisi terkini, bukan tick broker atau rekod point-in-time; news sejarah tidak digunakan.

| Kaedah | Zone dikesan | Signal | Trade masuk | Menang / kalah | Hasil kasar |
|---|---:|---:|---:|---|---:|
| V3 asal | — | 3 | 1 | 0 / 1 | -32.877 pips |
| Order Block M15 | 36 | 1 | 1 | 0 / 1 | -63.789 pips |
| FVG M15 | 44 | 1 | 1 | 0 / 1 | -63.789 pips |

**OB dan FVG memberi signal pada peluang BUY yang sama. Ini satu peluang unik, bukan dua bukti bebas. Jangan jumlahkan hasil kedua-duanya.** Dua signal V3 tidak memenuhi syarat fill dan tidak dikira kalah. Kedua-dua strategi POI menggunakan pengesanan setup alternatif, bukan penapis signal V3.

## Bukti urutan zone → sentuhan → confirmation → entry

Semua waktu di bawah ialah MYT:

- OB disahkan pada 21 Oktober 23:30, zone 4126.77451–4131.54850.
- FVG disahkan pada 23:45, zone 4131.54850–4131.63129.
- Candle sentuhan pertama tutup pada 23:50.
- Confirmation M5 dan signal BUY pada 23:55, selepas sentuhan.
- Fill simulasi pada 22 Oktober 00:00: 4132.38891; SL 4126.01; TP2 4146.75.
- SL direkod pada candle yang tutup 00:40. Kerugian kasar 63.789 pips, kira-kira 1R.

FVG tersebut sangat kecil (kira-kira 0.83 pip). Eksperimen ini belum menetapkan lebar minimum FVG. Ini batasan reka bentuk yang perlu dipra-tetapkan dan diuji pada data baharu, bukan dilaras untuk membetulkan satu trade ini.

Kos ilustrasi 5/10 pips round-trip menjadikan hasil setiap portfolio POI -68.789/-73.789 pips. Kos broker sebenar belum dikonfigurasi. Win rate daripada satu trade tidak boleh digeneralisasikan.

## Peraturan dibekukan sebelum replay

- FVG: gap tiga candle M15; candle tengah mesti searah dan badannya sekurang-kurangnya 1.5 kali median badan 10 candle sebelumnya. Zone hanya diketahui selepas candle ketiga tutup.
- OB: candle terakhir yang berlawanan arah dalam tiga candle sebelum displacement. Displacement mesti searah, badan sekurang-kurangnya 1.5 kali median 10 badan sebelumnya dan close melepasi high/low lima candle sebelumnya. Zone menggunakan keseluruhan high–low candle OB; diketahui selepas displacement tutup.
- Setiap zone disimpan, luput selepas 4 jam, dan terbatal apabila M5 close melepasi sempadan luar. Candle pembentukan tidak boleh dikira sebagai retest.
- Selepas sentuhan pertama, tunggu confirmation pada salah satu daripada tiga candle M5 berikutnya. Confirmation mesti close keluar semula melalui sempadan dekat, memenuhi confirmation M5 V3, dan searah struktur H1 terkini.
- Setiap zone hanya satu signal. Jika beberapa zone jenis sama layak pada candle yang sama, pilih yang terbaru dan tamatkan yang lain.
- Kekalkan pembina plan V3, risk 20–80 pips, RR TP2 minimum 2 dan had entry 40 pips. SL juga mesti berada di luar zone. Score/setup V3 tidak digunakan kerana ini strategi setup alternatif; tiada skor rekaan diberi.
- Fill pada open M5 penuh selepas alert simulasi lewat 1 saat; validasi fill asal; TP2 full exit; SL dahulu jika TP2/SL disentuh dalam candle sama. Bukan simulasi tick.

## Keadaan zone selepas replay

OB: 18 tamat tempoh confirmation, 8 luput, 8 terbatal, 1 bersignal, 1 masih menunggu.
FVG: 21 tamat tempoh confirmation, 17 terbatal, 3 luput, 1 bersignal, 2 masih menunggu.
Bilangan ini termasuk zone semasa warm-up, jadi bukan semua zone layak untuk trading.

## Kesimpulan dan batasan

Konsep persistent zone kini benar-benar diuji: zone terbentuk dahulu, menunggu retest, kemudian confirmation. Dalam sampel pendek ini ia tidak mengatasi V3; hanya satu peluang unik memenuhi semua syarat dan kalah. Tiada tuning parameter atau holdout bebas. Hasil ini tidak membuktikan semua OB/FVG tidak berkesan.

Cadangan: kekalkan sebagai eksperimen. Langkah berikut ialah rekod prospective zone dan sebab penolakan pada data baharu dengan peraturan dibekukan; hanya bandingkan prestasi selepas sampel mencukupi. Kod ini tidak diimport oleh engine live, tidak mengubah Learning Journal, dan tidak menghantar Telegram.

## Ulang kajian

Dari root repository: `python research/poi_zones/run_study.py`.
Input: `research/poi/input.csv`; baseline: `research/poi/results.json`.
Output lengkap: `research/poi_zones/results.json` (hash input, parameter, semua zone dan trade).
Ujian: `python -m unittest discover -s tests -v`.

Validasi: 120 ujian Python dan semakan dashboard lulus. Ujian prefix memotong data pada waktu keputusan, sebelum fill/SL; pengesanan zone dan signal kekal sama tanpa candle masa depan.
