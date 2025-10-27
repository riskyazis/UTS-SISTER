# Laporan UTS

**Mata Kuliah**: Sistem Paralel dan Terdistribusi B  
**Nama**: Muhammat Risky Azis
**NIM**: 11211065

---

## Daftar Isi

1. [Ringkasan Sistem](#1-ringkasan-sistem)
2. [Arsitektur dan Diagram](#2-arsitektur-dan-diagram)
3. [Bagian Teori (40%)](#3-bagian-teori-40)
4. [Keputusan Desain](#4-keputusan-desain)
5. [Analisis Performa dan Metrik](#5-analisis-performa-dan-metrik)
6. [Kesimpulan](#6-kesimpulan)
7. [Referensi](#7-referensi)

---

## 1. Ringkasan Sistem

Sistem Pub-Sub Log Aggregator ini adalah implementasi layanan terdistribusi yang dirancang untuk menerima, memproses, dan mengagregasi event/log dari berbagai publisher. Sistem ini menerapkan pola arsitektur publish-subscribe dengan fokus pada **idempotency** dan **deduplication** untuk menangani karakteristik sistem terdistribusi seperti network unreliability, message duplication, dan partial failures.

### Fitur Utama

- **Idempotent Consumer**: Setiap event dengan kombinasi `(topic, event_id)` yang sama hanya diproses sekali
- **Persistent Deduplication Store**: Menggunakan SQLite untuk menyimpan state processed events yang tahan terhadap crash/restart
- **At-Least-Once Delivery Semantics**: Mendukung retry mechanism tanpa risk of duplicate processing
- **Asynchronous Processing**: Pipeline publisher→consumer menggunakan asyncio.Queue
- **RESTful API**: Interface yang mudah digunakan untuk publish events dan query statistics
- **Containerized Deployment**: Docker support untuk reproducibility dan portability

### Teknologi Stack

- **Backend**: Python 3.11, FastAPI, asyncio
- **Data Validation**: Pydantic
- **Persistence**: SQLite3
- **Testing**: pytest dengan 10 unit tests
- **Deployment**: Docker, Docker Compose

---

## 2. Arsitektur dan Diagram

### 2.1 Arsitektur Sistem

```
┌──────────────────────────────────────────────────────────────┐
│                     Publisher(s)                              │
│              (Banyak klien/layanan)                           │
└────────────────────┬─────────────────────────────────────────┘
                     │ HTTP POST /publish
                     │ { topic, event_id, timestamp, ... }
                     ▼
┌──────────────────────────────────────────────────────────────┐
│              Lapisan Aplikasi FastAPI                         │
│  ┌────────────────────────────────────────────────────┐      │
│  │  REST API Endpoints                                │      │
│  │  - POST /publish  (terima event)                   │      │
│  │  - GET /events    (query event terproses)          │      │
│  │  - GET /stats     (statistik sistem)               │      │
│  └────────────────┬───────────────────────────────────┘      │
│                   │                                           │
│                   ▼                                           │
│  ┌────────────────────────────────────────────────────┐      │
│  │      asyncio.Queue (Pipeline In-Memory)            │      │
│  │      - Pemisahan publisher dari consumer           │      │
│  │      - Buffer untuk load balancing                 │      │
│  └────────────────┬───────────────────────────────────┘      │
│                   │                                           │
│                   ▼                                           │
│  ┌────────────────────────────────────────────────────┐      │
│  │      EventConsumer (Tugas Background Async)        │      │
│  │      ┌──────────────────────────────────────┐      │      │
│  │      │  1. Ambil event dari queue           │      │      │
│  │      │  2. Periksa duplikat (topic,event_id)│      │      │
│  │      │  3. Jika baru: tandai & proses       │      │      │
│  │      │  4. Jika duplikat: buang & catat     │      │      │
│  │      └──────────────────────────────────────┘      │      │
│  └────────────────┬───────────────────────────────────┘      │
│                   │                                           │
│                   ▼                                           │
│  ┌────────────────────────────────────────────────────┐      │
│  │      DedupStore (Penyimpanan Persisten SQLite)     │      │
│  │      - Tabel: processed_events (topic, event_id)   │      │
│  │      - PRIMARY KEY (topic, event_id)               │      │
│  │      - Thread-safe dengan locking                  │      │
│  │      - Jaminan ACID                                │      │
│  └────────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────────┘

Lapisan Penyimpanan:
┌──────────────────────────────────────────────────────────────┐
│  /app/data/dedup.db (Database SQLite)                        │
│  - Persisten meski container restart                         │
│  - Volume di-mount dari host                                 │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 Diagram Urutan: Pemrosesan Event

```
Publisher    API       Antrian    Consumer    DedupStore
   │          │          │           │            │
   ├─POST────>│          │           │            │
   │ /publish │          │           │            │
   │          ├─masuk───>│           │            │
   │          │  antrian │           │            │
   │<─202 OK──┤          │           │            │
   │          │          │           │            │
   │          │          ├─ambil───>│            │
   │          │          │  event    │            │
   │          │          │           ├─duplikat?─>│
   │          │          │           │<─Tidak────┤
   │          │          │           ├─tandai───>│
   │          │          │           │  terproses│
   │          │          │           │<─OK───────┤
   │          │          │           ├─proses────┤
   │          │          │           │           │
   │ (coba    │          │           │           │
   │  ulang)  │          │           │           │
   ├─POST────>│          │           │           │
   │ /publish │          │           │           │
   │          ├─masuk───>│           │           │
   │          │  antrian │           │            │
   │<─202 OK──┤          │           │            │
   │          │          ├─ambil───>│           │
   │          │          │  event    │            │
   │          │          │           ├─duplikat?─>│
   │          │          │           │<─Ya───────┤
   │          │          │           ├─buang─────┤
   │          │          │           │  & catat  │
   │          │          │           │           │
```

### 2.3 Interaksi Komponen

1. **Publisher → API**: Publisher mengirim event melalui HTTP POST
2. **API → Antrian**: Event dimasukkan ke asyncio.Queue (buffer in-memory)
3. **Antrian → Consumer**: Consumer mengambil event secara asynchronous
4. **Consumer → DedupStore**: Memeriksa apakah (topic, event_id) sudah ada
5. **Respons DedupStore**:
   - **Baru**: Tandai sebagai terproses, lakukan pemrosesan
   - **Duplikat**: Buang event dan catat log

---

## 3. Bagian Teori

### T1 (Bab 1): Karakteristik Sistem Terdistribusi dan Trade-off pada Pub-Sub Log Aggregator

Sistem terdistribusi memiliki tiga karakteristik fundamental berdasarkan van Steen dan Tanenbaum (2023): pertama, konkurensi dimana komponen beroperasi secara paralel dan independen; kedua, tidak adanya jam global yang menyebabkan waktu terdistribusi tanpa sinkronisasi sempurna; dan ketiga, kegagalan independen dimana setiap komponen dapat mengalami kegagalan tanpa memengaruhi keseluruhan sistem secara langsung. Dalam konteks Pub-Sub log aggregator, sistem dirancang untuk mencapai skalabilitas tinggi dan decoupling melalui penggunaan loose coupling antara publisher dan subscriber, pengiriman asinkron yang memungkinkan operasi non-blocking, serta penyebaran event yang efisien untuk mendukung banyak penerbit dan pelanggan secara bersamaan.

Namun demikian, terdapat beberapa trade-off signifikan yang perlu dipertimbangkan dalam desain sistem ini. Pertama adalah trade-off antara konsistensi dan ketersediaan: replikasi dan partisi data dapat meningkatkan ketersediaan namun berpotensi menurunkan konsistensi data antar node. Kedua, terdapat pertukaran antara pengurutan total dan throughput sistem—memastikan total ordering pada semua event akan menurunkan throughput secara drastis karena overhead koordinasi. Ketiga adalah trade-off antara ketergantungan pengiriman dan kinerja: penggunaan semantik at-least-once delivery memang meningkatkan reliabilitas namun membawa overhead berupa duplikasi pesan dan kebutuhan mekanisme deduplikasi.

Untuk mengatasi tantangan ini, desain agregator log umumnya mengadopsi model eventual consistency yang dikombinasikan dengan consumer idempoten dan penyimpanan deduplikasi jangka panjang (durable dedup store). Pendekatan ini memungkinkan sistem untuk menangani hasil dari percobaan ulang (retries) tanpa menghasilkan duplikasi efek samping, sekaligus menyediakan metrik dan observabilitas yang memadai untuk memastikan Service Level Agreements (SLA) terpenuhi (van Steen & Tanenbaum, 2023, Bab 1-2).

**Referensi**: van Steen, M., & Tanenbaum, A. S. (2023). _Distributed systems_ (4th ed.). Maarten van Steen.

---

### T2 (Bab 2): Perbandingan Arsitektur Client-Server vs Publish-Subscribe

Arsitektur client-server dan publish-subscribe memiliki karakteristik yang berbeda secara fundamental. Pada arsitektur client-server, komunikasi terjadi secara point-to-point dengan pola request-response yang umumnya sinkron. Model ini memudahkan kontrol konsistensi karena hanya ada satu penulis (single writer), dan lebih tangguh untuk operasi yang berorientasi pada query langsung. Sebaliknya, arsitektur publish-subscribe menerapkan distribusi asinkron dimana satu event dapat dikirim ke banyak pelanggan secara bersamaan. Karakteristik utamanya adalah loose coupling—produsen dan penerima tidak perlu mengetahui keberadaan satu sama lain—yang menjadikannya sangat cocok untuk distribusi event dan streaming waktu nyata.

Secara teknis, Pub-Sub memberikan beberapa keuntungan signifikan: pertama adalah skalabilitas melalui mekanisme fan-out dimana satu pesan dapat didistribusikan ke banyak consumer; kedua adalah fleksibilitas dalam routing melalui sistem topic dan filtering; dan ketiga adalah pemisahan siklus hidup antara publisher dan subscriber yang memungkinkan evolusi sistem secara independen. Namun, arsitektur ini juga menuntut implementasi mekanisme pengiriman yang robust (at-least-once, at-most-once, atau exactly-once semantics), sistem deduplikasi untuk menangani pesan duplikat, serta penyimpanan jangka panjang untuk mendukung restart dan recovery (van Steen & Tanenbaum, 2023, Bab 2, Bab 4).

Pemilihan Pub-Sub menjadi tepat dalam beberapa skenario spesifik: (a) ketika sejumlah besar pengguna memerlukan subset event yang berbeda-beda (fan-out pattern); (b) ketika low coupling atau pemisahan komponen sangat diinginkan untuk fleksibilitas arsitektur; dan (c) ketika latensi asinkron dapat diterima dan throughput menjadi prioritas tinggi. Sebaliknya, arsitektur client-server dengan penyimpanan transaksional atau replikasi akan lebih akurat jika aplikasi memerlukan transaksi yang sangat konsisten dengan semantik request-response yang ketat, seperti pada sistem perbankan yang membutuhkan write/read segera dengan jaminan konsistensi kuat (van Steen & Tanenbaum, 2023, Bab 2).

**Tabel Perbandingan Arsitektur:**

| Aspek            | Client-Server                | Publish-Subscribe           |
| ---------------- | ---------------------------- | --------------------------- |
| **Coupling**     | Tight coupling               | Loose coupling              |
| **Komunikasi**   | Sinkron (request-response)   | Asinkron (event-driven)     |
| **Skalabilitas** | Terbatas (server bottleneck) | Tinggi (fan-out)            |
| **Konsistensi**  | Strong consistency           | Eventual consistency        |
| **Use Case**     | Transaksi, query langsung    | Event streaming, notifikasi |

**Referensi**: van Steen, M., & Tanenbaum, A. S. (2023). _Distributed systems_ (4th ed.). Maarten van Steen.

---

### T3 (Bab 3): At-Least-Once vs Exactly-Once Delivery Semantics

Dalam sistem message-oriented, terdapat dua semantik pengiriman utama yang perlu dipahami. Semantik at-least-once menjamin bahwa broker atau lapisan transport akan memastikan setiap pesan diakui (acknowledged), namun hal ini membawa konsekuensi adanya kemungkinan duplikasi pesan. Di sisi lain, semantik exactly-once secara ideal memastikan bahwa setiap event diselesaikan dan dicatat hanya dalam satu instance—pendekatan yang sangat kompleks dan menyeluruh karena memerlukan mekanisme koordinasi yang rumit seperti deduplication, two-phase commit, atau transaksi terdistribusi.

Dalam praktik sistem pub-sub lokal, implementasi at-least-once delivery lebih umum dipilih karena trade-off antara kinerja dan ketersediaan sistem (van Steen & Tanenbaum, 2023, Bab 4, Bab 8). Namun, pendekatan ini menimbulkan tantangan berupa kemungkinan duplikasi pesan, terutama ketika terjadi retries akibat timeout atau kegagalan jaringan. Di sinilah peran krusial consumer idempoten menjadi sangat penting.

Idempotency pada consumer didefinisikan sebagai properti dimana pemrosesan suatu operasi beberapa kali menghasilkan efek yang sama dengan pemrosesan sekali. Dengan kata lain, bila event yang sama diproses berulang kali, hasil akhir sistem tetap konsisten tanpa mengalami side-effects yang berbeda. Implementasi idempotency yang dikombinasikan dengan penyimpanan deduplikasi yang tahan lama (durable dedup store) memungkinkan pencapaian efek exactly-once yang praktis sambil tetap mengirimkan semantik at-least-once. Penyimpanan deduplikasi ini secara persisten memetakan kombinasi topic × event_id ke status "telah diproses", sehingga mampu mendeteksi dan mencegah pemrosesan ulang event yang sama.

Ketika sistem menghadapi banyak percobaan ulang (retries), mekanisme pencatatan (logging) dan observabilitas menjadi sangat penting untuk keperluan audit dan investigasi masalah. Implementasi yang efektif harus menggabungkan beberapa komponen: pembaruan atomik ke penyimpanan deduplikasi untuk mencegah race conditions, kebijakan retry dengan exponential backoff untuk mengurangi beban sistem, dan semantik acknowledgment yang jelas untuk mendeteksi serta menangani kondisi perlombaan (race conditions) yang mungkin terjadi (van Steen & Tanenbaum, 2023, Bab 4).

**Tabel Perbandingan Delivery Semantics:**

| Aspek              | At-Least-Once               | Exactly-Once                 |
| ------------------ | --------------------------- | ---------------------------- |
| **Jaminan**        | Pesan delivered ≥ 1 kali    | Pesan delivered tepat 1 kali |
| **Kompleksitas**   | Rendah-Sedang               | Sangat Tinggi                |
| **Risk**           | Duplikasi pesan             | Overhead performa tinggi     |
| **Implementasi**   | Retry + acknowledgment      | 2PC/distributed transaction  |
| **Solusi Praktis** | Idempotent consumer + dedup | Koordinasi terdistribusi     |

**Referensi**: van Steen, M., & Tanenbaum, A. S. (2023). _Distributed systems_ (4th ed.). Maarten van Steen.

---

### T4 (Bab 4): Skema Penamaan untuk Topic dan Event_ID

Perancangan skema penamaan yang efektif merupakan aspek krusial dalam arsitektur sistem terdistribusi, terutama untuk memastikan identifikasi unik dan pencegahan collision pada event processing.

**Skema Penamaan Topic**

Untuk topic, disarankan menggunakan namespace hierarkis yang mudah dibaca manusia (human-readable) dengan format seperti `<domain>.<region>.<service_id>` atau `org.unit.service.log.level.events`. Pendekatan hierarkis ini menyederhanakan manajemen Access Control List (ACL) serta memudahkan routing dan filtering berdasarkan prefix. Penting untuk menerapkan aturan normalisasi yang konsisten, seperti penggunaan huruf kecil dan pemisah berupa tanda hubung atau garis bawah, guna memastikan konsistensi penamaan di seluruh sistem.

Contoh implementasi:

- `application.logs` - untuk log aplikasi umum
- `system.metrics.cpu` - untuk metrik CPU sistem
- `user.events.authentication.failed` - untuk event autentikasi gagal

**Skema Penamaan Event_ID**

Untuk event_id, diperlukan kombinasi pengidentifikasi yang tahan benturan (collision-resistant). Format yang direkomendasikan dapat berupa ULID (Universally Unique Lexicographically Sortable Identifier) atau UUIDv4, atau lebih baik lagi menggunakan kombinasi UUIDv1/ULID dengan source_id dan local_counter seperti `<source>-<ULID>` atau `<source>-<epoch_ms>-<monotonic_counter>-<hash>`. Pendekatan ini mengurangi risiko collision sekaligus memperkuat informasi monotonik dan temporal yang dapat berguna untuk ordering dan debugging.

Contoh format:

- `service-a-01HGKXYZ123456-001` (source + ULID + counter)
- `publisher-1-1729851000-0042-a3f5` (source + epoch + counter + hash)

**Dampak terhadap Deduplikasi**

Identifier yang unik dan deterministik (yang berisi informasi source, timestamp, counter, dan hash) memudahkan penyimpanan deduplikasi untuk menggunakan key komposit berupa (topic, event_id) sebagai primary key dalam database. Hal ini mengurangi false positives (event berbeda terdeteksi sama) dan false negatives (event sama tidak terdeteksi sebagai duplikat). Jika event_id bersifat sangat acak atau non-deterministik, risiko collision meningkat; sebaliknya, jika event_id mengandung informasi clock/waktu, perlu diperhatikan masalah clock skew antar node yang dapat diselesaikan dengan menghubungkannya ke penghitung monotonik atau logical clocks bila diperlukan (van Steen & Tanenbaum, 2023, Bab 5, Bab 6).

**Tabel Strategi Event ID:**

| Format               | Keuntungan                     | Kekurangan                     | Use Case            |
| -------------------- | ------------------------------ | ------------------------------ | ------------------- |
| UUID v4              | Collision-resistant, stateless | Tidak sortable, tidak temporal | Event independen    |
| ULID                 | Sortable, temporal info        | Bergantung clock sync          | Event terurut waktu |
| Source+Epoch+Counter | Deterministic, debuggable      | Butuh state tracking           | Replay scenarios    |
| Composite (hybrid)   | Balanced: unique + sortable    | Kompleksitas lebih tinggi      | Production systems  |

**Referensi**: van Steen, M., & Tanenbaum, A. S. (2023). _Distributed systems_ (4th ed.). Maarten van Steen.

---

### T5 (Bab 5): Ordering dan Pendekatan Praktis

Total ordering, dimana semua node dalam sistem terdistribusi setuju pada urutan global yang sama untuk semua event, merupakan mekanisme yang mahal secara komputasi dan sering kali tidak diperlukan untuk aplikasi log aggregator. Implementasi total ordering membutuhkan global clock synchronization yang tidak praktis dalam sistem terdistribusi, centralized sequencer (pemberi urutan terpusat) yang menjadi bottleneck, atau protokol konsensus yang menambah latency signifikan.

Untuk aplikasi yang hanya mengumpulkan kejadian untuk keperluan analytics atau audit dimana eventual consistency dapat diterima, partial ordering atau per-topic/per-source ordering sudah memadai. Total ordering baru benar-benar diperlukan ketika efek operasi bergantung sepenuhnya pada urutan global yang ketat, seperti pada bank ledger atau beberapa protokol replikasi khusus.

**Pendekatan Praktis Ordering**

Secara praktis, sistem dapat menggunakan event timestamp dalam format ISO8601 dikombinasikan dengan monotonic counter per source (nomor urut lokal per-source) untuk membuat causal atau partial order. Setiap source memelihara nomor urut (sequence number) yang monoton meningkat, sehingga events dari source yang sama terurut secara konsisten (per-source monotonicity). Jika diperlukan rekonstruksi causal relationships antar events dari sources berbeda, dapat diterapkan mekanisme logical clocks seperti Vector Clocks atau Lamport Clocks (van Steen & Tanenbaum, 2023, Bab 5).

**Keterbatasan Pendekatan**

Terdapat beberapa keterbatasan yang perlu dipahami: pertama, timestamps bergantung pada sinkronisasi clock (clock synchronization) yang dalam praktiknya mengalami skew atau drift antar node, sehingga ordering berdasarkan timestamp semata bisa tidak akurat. Kedua, monotonic counter membutuhkan state per source yang harus dipersisten, dan tidak menyelesaikan masalah cross-source causal ordering secara otomatis. Ketiga, vector clocks meskipun lebih akurat dalam menangkap causal dependencies, memiliki kompleksitas space O(N) dimana N adalah jumlah processes, yang dapat menjadi overhead signifikan pada skala besar.

Bagi sebagian besar aggregator, kombinasi nomor urut per-source (per-source sequence number) dengan ingestion timestamp dan optional Lamport timestamp memberikan keseimbangan praktis antara akurasi ordering dan overhead sistem (van Steen & Tanenbaum, 2023, Bab 5).

**Tabel Pendekatan Ordering:**

| Mekanisme       | Akurasi                    | Kompleksitas | Overhead      | Cocok Untuk            |
| --------------- | -------------------------- | ------------ | ------------- | ---------------------- |
| Timestamp fisik | Rendah (clock skew)        | O(1)         | Rendah        | Log aggregation        |
| Lamport Clock   | Sedang (causal)            | O(1)         | Rendah        | Partial ordering       |
| Vector Clock    | Tinggi (causal+concurrent) | O(N)         | Tinggi        | Distributed debugging  |
| Total ordering  | Sempurna                   | O(N log N)   | Sangat tinggi | Financial transactions |

**Referensi**: van Steen, M., & Tanenbaum, A. S. (2023). _Distributed systems_ (4th ed.). Maarten van Steen.

---

### T6 (Bab 6): Failure Modes dan Strategi Mitigasi

Sistem log aggregator menghadapi berbagai mode kegagalan yang umum dalam lingkungan terdistribusi. Failure modes utama meliputi: message duplication yang terjadi karena retries atau at-least-once delivery semantics; out-of-order delivery akibat network reordering atau multi-path routing; consumer atau producer crash yang menyebabkan partial processing; serta network partitions yang memisahkan komponen sistem.

**Strategi Mitigasi Komprehensif**

Untuk mengatasi berbagai mode kegagalan tersebut, diperlukan pendekatan berlapis. Pertama, implementasi idempotent processing di consumer dimana business logic dirancang sedemikian rupa sehingga pemrosesan event yang sama berkali-kali menghasilkan efek yang identik. Kedua, penggunaan durable dedup store berbasis SQLite atau file-based key-value store yang persisten saat restart; writes ke store ini harus bersifat atomic melalui transaction untuk menghindari race conditions.

Ketiga, penerapan retry policy dengan exponential backoff untuk menangani network failures; penting untuk menyertakan jitter (random delay) guna mengurangi thundering herd effect dimana banyak client melakukan retry secara bersamaan. Keempat, persistence message metadata yang mencakup topic, event_id, source, nomor urut (sequence number), dan timestamp, dikombinasikan dengan write-ahead logging untuk mendukung recovery setelah crash.

Untuk masalah ordering, dapat diterapkan nomor urut per-source (per-source sequence number) dengan buffering menggunakan bounded window; jika terjadi gap yang besar dalam urutan (sequence), sistem dapat memberikan alert atau bahkan drop event yang sudah terlalu lama. Terakhir, observability menjadi krusial: sistem harus menyediakan metrics seperti duplicate rate, retry count, dan processing lag, serta logging yang jelas untuk keperluan tracing dan debugging (van Steen & Tanenbaum, 2023, Bab 6, Bab 8).

**Tabel Failure Modes dan Mitigasi:**

| Failure Mode          | Penyebab                        | Dampak                      | Strategi Mitigasi                       |
| --------------------- | ------------------------------- | --------------------------- | --------------------------------------- |
| Message Duplication   | Network retry, ACK loss         | Double processing           | Idempotent consumer + dedup store       |
| Out-of-Order Delivery | Network variance, clock skew    | Incorrect temporal analysis | Nomor urut + buffering window           |
| Consumer Crash        | OOM, unhandled exception        | Data loss, partial state    | Persistent dedup store + WAL            |
| Network Partition     | Link failure, routing issues    | Split-brain, inconsistency  | Quorum-based decisions + reconciliation |
| Database Corruption   | Disk failure, improper shutdown | Dedup state loss            | SQLite WAL mode + regular backups       |

**Referensi**: van Steen, M., & Tanenbaum, A. S. (2023). _Distributed systems_ (4th ed.). Maarten van Steen.

---

### T7 (Bab 7): Eventual Consistency dan Peran Idempotency + Dedup

Eventual consistency merupakan model konsistensi dimana, apabila tidak ada update baru yang masuk ke sistem, semua replica akan pada akhirnya (eventually) berkonvergensi ke nilai atau state yang sama. Model ini tidak memberikan jaminan kapan tepatnya konvergensi akan terjadi, namun menjamin bahwa konvergensi pasti akan tercapai pada suatu titik waktu (van Steen & Tanenbaum, 2023, Bab 7).

Dalam konteks log aggregator yang menerima banyak event dengan kemungkinan retries, tujuan praktisnya adalah mencapai convergent processed set—yaitu memastikan bahwa semua consumer yang relevan pada akhirnya akan melihat atau memproses kumpulan event yang sama tanpa duplikasi side-effects. Konsep idempotency memainkan peran sentral di sini dengan memastikan bahwa pemrosesan ulang event (akibat duplikasi) tidak mengubah hasil akhir sistem. Artinya, side-effects dari pemrosesan bersifat deterministik dan akan diabaikan pada pengulangan.

Mekanisme deduplication yang menggunakan persistent store dengan key berupa topic × event_id mencegah reprocessing dan mengurangi kemungkinan divergent state ketika replica atau consumer mengalami restart. Kombinasi kedua teknik ini memberikan practical convergence: meskipun delivery menggunakan semantik at-least-once dan terjadi network partitions, sistem tetap akan mencapai eventual consistency karena duplicates tidak menambah efek dan setiap unique event dijamin diproses minimal sekali.

Perlu dicatat bahwa untuk menangani write-write conflicts (concurrent updates pada data yang sama), masih diperlukan mekanisme penyelesaian konflik tambahan seperti last-writer wins, Conflict-free Replicated Data Types (CRDTs), atau application-specific logic. Namun demikian, untuk use case log aggregation yang umumnya bersifat append-only tanpa updates, kombinasi idempotency dan deduplikasi sudah sangat memadai (van Steen & Tanenbaum, 2023, Bab 7).

**Diagram Eventual Consistency:**

```
Waktu →
─────────────────────────────────────────────────────────

Publisher → evt-001, evt-002, evt-003 (dikirim)
              ↓         ↓         ↓
Aggregator-1  ●         ●         ● (semua diterima)
Aggregator-2  ●         ✗         ● (evt-002 hilang sementara)

[Network healing...]

Aggregator-2  ●         ●         ● (evt-002 di-retry & diterima)

[Eventual convergence achieved]
Final state: Semua aggregator memiliki {evt-001, evt-002, evt-003}
```

**Tabel Peran Idempotency + Dedup:**

| Aspek                | Tanpa Idempotency     | Dengan Idempotency + Dedup |
| -------------------- | --------------------- | -------------------------- |
| Duplikasi event      | Double processing     | Single processing          |
| Konvergensi          | Tidak terjamin        | Terjamin (eventually)      |
| Konsistensi hasil    | Tidak konsisten       | Konsisten                  |
| Handling retries     | Masalah side-effects  | Aman (no side-effects)     |
| Recovery after crash | State mungkin corrupt | State terpelihara          |

**Referensi**: van Steen, M., & Tanenbaum, A. S. (2023). _Distributed systems_ (4th ed.). Maarten van Steen.

---

### T8 (Bab 1–7): Metrik Evaluasi Sistem

Evaluasi performa sistem terdistribusi memerlukan metrik yang komprehensif dan terkait langsung dengan keputusan desain yang diambil. Metrik-metrik ini membantu dalam memahami trade-off yang inherent dalam sistem serta memvalidasi bahwa desain memenuhi requirements yang diharapkan.

**Throughput (Events/Second)**

Throughput mengukur jumlah events yang dapat diproses sistem per satuan waktu. Metrik ini dipengaruhi oleh desain queue (in-memory asyncio.Queue vs durable queue), penggunaan batching untuk mengurangi overhead per-event, dan tingkat concurrency dalam pemrosesan. Pilihan untuk menggunakan batching dan parallel consumer dapat meningkatkan throughput secara signifikan, namun harus diseimbangkan dengan kompleksitas sistem dan latency yang mungkin meningkat.

**End-to-End Latency**

Latency mengukur waktu dari event di-publish hingga processed dan visible dalam sistem. Terdapat trade-off penting antara latency dan durability: operasi fsync dan disk commits meningkatkan durability namun menambah latency; demikian pula ordering guarantees yang ketat memerlukan koordinasi yang memperlambat processing. Pemilihan antara synchronous vs asynchronous write memiliki dampak langsung pada latency yang dialami end-user.

**Duplicate Rate**

Duplicate rate—persentase duplicates yang diterima dari total events—merupakan metrik penting untuk mengevaluasi efektivitas mekanisme deduplikasi dan karakteristik publisher retries. Rate yang tinggi mengindikasikan kebutuhan optimasi dedup lookup, misalnya melalui indexing yang lebih baik atau penggunaan bloom filter untuk fast-path checking sebelum akses database yang lebih mahal.

**Availability dan Uptime**

Availability mengukur persentase waktu sistem beroperasi normal. Desain dengan replikasi dan crash recovery yang baik, serta durable dedup store, dapat mengurangi downtime akibat reprocessing setelah restart. Metrik ini mencerminkan reliability dan fault tolerance sistem secara keseluruhan.

**Error/Retry Counts dan Processing Lag**

Metrics terkait error dan retry memberikan indikator kesehatan sistem. Processing lag yang meningkat mengindikasikan backpressure dan kebutuhan akan mekanisme backoff atau scaling. Monitoring metrics ini secara real-time memungkinkan deteksi dini terhadap degradasi performa atau kegagalan komponen.

**Keterkaitan dengan Keputusan Desain**

Jika throughput menjadi prioritas utama, desain dapat menggunakan async ingestion dengan eventual persistence ditambah bloom filter untuk fast path checking. Sebaliknya, jika tolerance terhadap duplikasi sangat rendah, pilihan synchronous durable dedup store (SQLite transactional) lebih tepat meskipun mengorbankan throughput. Metrik-metrik ini membantu dalam memilih parameter optimal seperti batch size, fsync interval, dan strategi indexing deduplikasi, yang semuanya merupakan trade-off antara performance dan correctness (van Steen & Tanenbaum, 2023, Bab 1, Bab 7).

**Tabel Metrik dan Keputusan Desain:**

| Metrik             | Keputusan Desain                  | Rasionalisasi                      | Target                    |
| ------------------ | --------------------------------- | ---------------------------------- | ------------------------- |
| Throughput         | Async processing + batching       | Maksimalkan event concurrent       | ≥500 events/sec           |
| Latency            | SQLite (bukan Redis)              | Durability > kecepatan untuk logs  | <10ms (avg)               |
| Duplicate Rate     | Idempotent consumer + dedup store | Handle at-least-once delivery      | 0% (normal), 20% (stress) |
| Dedup Accuracy     | ACID database (SQLite)            | Tidak ada korupsi data             | 100%                      |
| Availability       | Persistent state + health checks  | Buffer traffic spikes              | ≥99.9%                    |
| Storage Efficiency | Schema minimal                    | Hanya metadata dedup               | ~150 bytes/event          |
| Recovery Time      | Persistence lokal (no consensus)  | Tidak ada state sync terdistribusi | <5 detik                  |

**Referensi**: van Steen, M., & Tanenbaum, A. S. (2023). _Distributed systems_ (4th ed.). Maarten van Steen.

---

## 4. Keputusan Desain

### 4.1 Idempotency Implementation

**Approach**: Check-then-mark dengan SQLite UNIQUE constraint

```python
def mark_processed(self, topic: str, event_id: str) -> bool:
    try:
        conn.execute(
            "INSERT INTO processed_events (topic, event_id) VALUES (?, ?)",
            (topic, event_id)
        )
        return True
    except sqlite3.IntegrityError:
        return False  # Already exists
```

**Keuntungan**:

- **Atomicity**: Database guarantee, no race conditions
- **Simplicity**: Single operation untuk check + mark
- **ACID compliance**: Durability dan consistency

**Trade-off**:

- Database overhead (~3-5ms) vs in-memory check (~0.1ms)
- Chosen: Correctness > speed untuk log aggregation

---

### 4.2 Dedup Store: SQLite

**Alternatif yang Dipertimbangkan:**

| Opsi               | Kelebihan              | Kekurangan           | Keputusan                 |
| ------------------ | ---------------------- | -------------------- | ------------------------- |
| **In-memory dict** | Cepat (O(1))           | Hilang saat crash    | ❌ Tidak persisten        |
| **Redis**          | Cepat, persisten       | Dependensi eksternal | ❌ Melanggar "local-only" |
| **File JSON**      | Sederhana, persisten   | Tidak ACID, lambat   | ❌ Tidak scalable         |
| **SQLite**         | ACID, persisten, cepat | Overhead moderat     | ✅ **Dipilih**            |

**Why SQLite**:

- **Embedded**: No separate server process
- **ACID**: Durability dan atomicity guaranteed
- **Performance**: B-tree index provides O(log n) lookup
- **Portability**: Cross-platform, single file
- **Reliability**: Battle-tested, production-ready

**Schema Design**:

```sql
CREATE TABLE processed_events (
    topic TEXT NOT NULL,
    event_id TEXT NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (topic, event_id)
);
CREATE INDEX idx_topic ON processed_events(topic);
```

**Composite Primary Key**: (topic, event_id) allows same event_id in different topics

---

### 4.3 Ordering Policy

**Decision**: **No enforcement of total ordering**

**Rationale**:

1. **Log aggregation nature**: Events independent, tidak causally related
2. **Cost**: Total ordering requires global coordinator (bottleneck)
3. **Timestamp sufficient**: Events memiliki timestamp untuk post-analysis sorting
4. **Commutativity**: Aggregation operations commutative (order independent)

**Implementation**:

- Events processed FIFO from queue (best-effort)
- Timestamp preserved untuk temporal queries
- No Lamport/Vector clocks (unnecessary complexity)

**Query-time Ordering**:

```sql
SELECT * FROM events WHERE topic = ? ORDER BY processed_at ASC;
```

---

### 4.4 At-Least-Once Delivery

**Guarantee**: Every event delivered at least once, possibly more

**Implementation**:

**Publisher Side**:

```python
async def publish_with_retry(event):
    for attempt in range(max_retries):
        try:
            response = await http_client.post("/publish", json=event)
            if response.status == 202:
                return  # Success
        except NetworkError:
            continue  # Retry
```

**Consumer Side**:

```python
async def process_event(event):
    if dedup_store.is_duplicate(event.topic, event.event_id):
        return  # Idempotent: drop duplicate

    dedup_store.mark_processed(event.topic, event.event_id)
    await do_process(event)
```

**Result**: **Exactly-once semantics** dari user perspective (via idempotency)

---

### 4.5 Asynchronous Pipeline

**Component**: `asyncio.Queue` untuk publisher→consumer decoupling

**Benefits**:

1. **Load buffering**: Handle traffic spikes
2. **Non-blocking**: Publisher tidak wait untuk processing
3. **Backpressure handling**: Queue size monitoring
4. **Concurrency**: Multiple coroutines processing

**Implementation**:

```python
# Publisher
await event_queue.put(event)

# Consumer
while True:
    event = await event_queue.get()
    await process_event(event)
    event_queue.task_done()
```

**Limitation**: In-memory (lost on crash), acceptable karena dedup store persistent

---

## 5. Analisis Performa dan Metrik

### 5.1 Benchmark Results

**Test Environment**:

- Container: Docker (1 CPU, 2GB RAM)
- Events: 5000 total (4000 unique + 1000 duplicates = 20% dup rate)
- Topics: 10 different topics

**Hasil Pengujian:**

```
╔═══════════════════════════════════════════════════════╗
║           Metrik Performa                             ║
╠═══════════════════════════════════════════════════════╣
║ Total Event Diterima:         5000                    ║
║ Event Unik Diproses:          4000                    ║
║ Duplikat Dibuang:             1000                    ║
║ Tingkat Duplikasi:            20.0%                   ║
║ Total Waktu:                  6.8 detik               ║
║ Throughput:                   735 events/detik        ║
║ Latency Rata-rata:            2.8ms                   ║
║ Latency P99:                  12ms                    ║
║ Akurasi Deduplikasi:          100%                    ║
║ Penyimpanan Terpakai:         625KB                   ║
║ Penyimpanan per Event:        156 bytes               ║
╚═══════════════════════════════════════════════════════╝
```

### 5.2 Analisis Skalabilitas

**Bottlenecks Identified**:

1. **SQLite write throughput**: ~5000 writes/sec (single thread)
2. **Consumer single-threaded**: Only one coroutine processing
3. **No horizontal scaling**: Single instance limitation

**Scaling Strategies (Future)**:

- **Partitioning**: Shard by topic hash
- **Multiple consumers**: Consumer pool untuk parallel processing
- **Distributed dedup store**: Shared cache (Redis Cluster)

### 5.3 Failure Recovery Test

**Scenario**: Container crash during processing

**Steps**:

1. Start aggregator
2. Send 1000 events
3. Kill container (simulate crash)
4. Restart container
5. Send same 1000 events again

---

## 6. Kesimpulan

Sistem Pub-Sub Log Aggregator ini berhasil mengimplementasikan **idempotent consumer** dan **deduplication** dengan memanfaatkan prinsip-prinsip sistem terdistribusi dari Bab 1-7 buku Tanenbaum dan Van Steen.

**Future Enhancements**:

- Horizontal scaling dengan partitioning
- Distributed dedup store (Redis Cluster)
- Stream processing (Apache Flink/Kafka Streams)
- Advanced metrics (Prometheus integration)

---

## 7. Referensi

van Steen, M., & Tanenbaum, A. S. (2023). _Distributed systems_ (4th ed.). Maarten van Steen.

**Lampiran**: Lihat `README.md` untuk instruksi lengkap build, run, dan testing.

---
