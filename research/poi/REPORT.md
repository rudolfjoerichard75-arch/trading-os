# Keputusan ujian awal POI Bosque V3

Status: INCONCLUSIVE — belum sesuai dijadikan penapis live.

Ujian menggunakan 1,028 candle M5 tersimpan, 18 Oktober 15:15 hingga 22 Oktober 04:50 MYT. Bahagian awal digunakan untuk warm-up; ketiga-tiga signal replay berlaku pada 21–22 Oktober MYT. Tiada jurang candle dalam data gabungan; 3 candle mempunyai revisi antara snapshot, dan revisi tersimpan terbaru digunakan.

| Kaedah | Signal dikekalkan | Trade masuk dan tutup | Menang | Kalah | Hasil kasar |
|---|---:|---:|---:|---:|---:|
| V3 asal | 3 | 1 | 0 | 1 | -32.877 pips |
| POI breakout/retest | 0 | 0 | — | — | Tiada trade |
| POI liquidity H1 | 0 | 0 | — | — | Tiada trade |
| Salah satu POI | 0 | 0 | — | — | Tiada trade |

Dua signal V3 tidak memenuhi syarat fill, jadi tidak dikira sebagai kalah. Satu trade yang masuk ialah SELL LIQUIDITY SWEEP pada 21 Oktober 12:30 MYT.

Dengan andaian kos round-trip 5 dan 10 pips, hasil trade V3 itu menjadi -37.877 dan -42.877 pips. Kos ini sensitiviti ilustrasi, bukan ukuran broker. Tiada win rate POI kerana penyebut trade ialah sifar.

Kesimpulan: penapis mengelakkan satu kerugian dalam sampel ini dengan menolak semua peluang. Itu belum membuktikan kelebihan keuntungan atau kesesuaian untuk live. Kekalkan dalam kajian; kumpul sampel prospektif lebih panjang tanpa menukar parameter berdasarkan satu keputusan.

Had: replay sejarah tanpa news; subset signal V3 asal, bukan simulasi rearm atau pencarian entry gantian; fill pada open M5 penuh selepas alert, had entry 40 pips asal, exit TP2 dan SL dahulu apabila candle ambigu. Data tersimpan bukan tick broker dan bukan set ujian bebas. Ini tidak mengukur prestasi AI terlatih.

Kod POI tidak diimport oleh engine live dan tidak menghantar Telegram. 111 ujian Python serta ujian dashboard lulus.

Lihat README.md untuk polisi tepat, input.csv untuk candle, results.json untuk manifest sumber dan setiap trade.
