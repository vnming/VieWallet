# Báo cáo Phase 0 — Product Discovery & Architecture

| Mục | Giá trị |
|---|---|
| Trạng thái | Hoàn tất và đã được duyệt (2026-11-21). Phase 1 đã bắt đầu. |
| Ngày | 2026-11-21 |
| Code đã viết | Không có (đúng thiết kế của Phase 0) |

## 1. Kết quả kiểm tra repository và môi trường

| Hạng mục | Kết quả | Ảnh hưởng |
|---|---|---|
| Repository | Một commit (`Initial commit`), chỉ có `README.md`. Không có code, không có quyết định trước đó. Remote `origin` trên GitHub. | Dự án mới hoàn toàn; không có gì cần giữ lại. |
| Python | 3.14.3 (`py`/`python`); không có alias `python3` trên Windows | Dùng `uv run` ở mọi nơi; script/tài liệu không dùng `python3`. |
| uv | 0.11.19 | Quản lý dependency backend. |
| Node / pnpm | Node 24.14.1, pnpm 9.15.4 | Workspace frontend. |
| Docker | **Đã cài** Docker Desktop: client/server 29.9.3, Compose v5.7.0, backend WSL 2. CLI ở `C:\Users\MINH\AppData\Local\Programs\DockerDesktop\resources\bin`. | Terminal/VS Code mở trước khi cài cần khởi động lại để nhận PATH. |
| Phần cứng | RAM 12 GB, NVIDIA RTX 4050 Laptop (6 GB VRAM), AMD Ryzen 5 7235HS | Đủ chạy model AI local ~3–8B lượng tử hóa; RAM là giới hạn khi chạy cùng Docker stack. |
| OS | Windows 11, PowerShell + Git Bash | Line ending (`.gitattributes`), xử lý đường dẫn trong script. |

## 2. Quyết định chính

| ID | Quyết định | Trạng thái | Tài liệu |
|---|---|---|---|
| D-01 | Modular monolith (process api + worker), ranh giới kiểm tra bằng import-linter | Đã duyệt | [ADR-0001](adr/0001-modular-monolith.md) |
| D-02 | Ledger kế toán kép bất biến, tài khoản HOLD cho tiền đang chờ, balance projection | Đã duyệt | [ADR-0002](adr/0002-double-entry-ledger.md) |
| D-03 | Tiền BIGINT đơn vị nhỏ nhất; chuỗi trong JSON; phí bps làm tròn half-up số nguyên | Đã duyệt | [ADR-0003](adr/0003-money-representation.md) |
| D-04 | READ COMMITTED + `FOR UPDATE` có thứ tự + chốt chặn DB CHECK/trigger | Đã duyệt | [ADR-0004](adr/0004-concurrency-control.md) |
| D-05 | State machine riêng cho từng loại giao dịch (báo giá/xác nhận hai bước cho trừ tiền) | Đã duyệt | [Xử lý tài chính §5](architecture/financial-processing.md#5-mô-hình-giao-dịch-và-state-machine) |
| D-06 | Simulator là FastAPI service riêng, có trang SimBank và callback có chữ ký | Đã duyệt | [ADR-0005](adr/0005-simulator-as-separate-service.md) |
| D-07 | Outbox + bảng job trong PostgreSQL; Redis chỉ cho rate limit/cache | Đã duyệt | [ADR-0006](adr/0006-postgres-outbox-and-jobs.md) |
| D-08 | Monorepo pnpm; app admin tách riêng (không phải PWA) | Đã duyệt | [ADR-0007](adr/0007-frontend-monorepo-separate-admin.md) |
| D-09 | JWT 10 phút trong bộ nhớ + refresh cookie xoay vòng + session phía server; TOTP cho nhân viên | Đã duyệt | [ADR-0008](adr/0008-authentication-tokens-sessions.md) |
| D-10 | AI: tự chạy vòng lặp tool, **chỉ model miễn phí** (Ollama local mặc định, free cloud tùy chọn, fake cho test), tool chỉ-đọc, data card | Đã duyệt | [ADR-0009](adr/0009-ai-orchestration.md) |
| D-11 | Python 3.14, PostgreSQL 18, Node 24 | Đã duyệt | [ADR-0010](adr/0010-runtime-versions.md) |
| D-12 | Hạ tầng local bằng Docker Desktop (WSL 2) | **Đã chốt** (đã cài) | [ADR-0010](adr/0010-runtime-versions.md) |
| D-13 | Tài liệu tiếng Việt; code/commit tiếng Anh; UI ưu tiên tiếng Việt | **Đã chốt** | [ADR-0011](adr/0011-documentation-language.md) |
| D-14 | Đổi roadmap: lát cắt dọc; analytics trước AI; lõi phê duyệt ở Phase 3; thông báo in-app ở Phase 4 | Đã duyệt | [Roadmap §1](roadmap.md#1-thay-đổi-so-với-roadmap-tham-khảo-đề-xuất) |
| D-15 | Không phát sinh chi phí: không có API trả phí; hosting Phase 13 ưu tiên miễn phí | **Đã chốt** | [AI §3](architecture/ai-assistant.md#3-chọn-model-và-cấu-hình) |
| D-16 | GitHub: CI trên GitHub Actions ở remote hiện có; không push hay đổi chế độ hiển thị repo khi chưa có chỉ thị | Đã chốt: CI trên GitHub Actions, repo public | — |

## 3. Giả định

Mặc định hợp lý, áp dụng trừ khi chủ dự án nói khác:

1. Chỉ dùng VND (exponent 0); kiến trúc sẵn sàng đa tiền tệ.
2. Mỗi khách hàng một ví chính; merchant có một ví merchant.
3. Email là kênh OTP/xác minh duy nhất, gửi về Mailpit (không có nhà cung cấp email/SMS thật).
4. SĐT là tùy chọn và chỉ dùng để tra cứu người nhận (giả lập, không xác minh qua SMS).
5. Merchant là tài khoản mô phỏng, tạo bằng seed hoặc admin; không có luồng đăng ký merchant công khai.
6. KYC dùng dữ liệu hư cấu và ảnh mẫu; hai cấp (0 chưa xác minh, 1 đã xác minh).
7. Múi giờ nghiệp vụ `Asia/Ho_Chi_Minh` cho hạn mức ngày/tháng và báo cáo; lưu trữ UTC.
8. Phí mặc định bằng 0 trừ phí rút tiền demo (1.100 ₫), tất cả cấu hình được.
9. Hạn mức mặc định như trong [Xử lý tài chính §8.2](architecture/financial-processing.md#82-kiểm-tra-hạn-mức).
10. Nạp và rút là bội của 1.000 ₫; chuyển/thanh toán là số nguyên đồng dương bất kỳ.
11. Chuyển tiền P2P không hoàn được; sửa sai bằng đảo giao dịch (nhân viên) hoặc lệnh chuyển mới.
12. Tài khoản nhân viên không sở hữu ví khách hàng (tách nhiệm vụ).
13. Trợ lý AI trả lời theo ngôn ngữ UI của user; không tư vấn đầu tư hay tín dụng.
14. Dữ liệu seed demo được sinh qua service nên tuân theo quy tắc ledger.
15. Chưa chọn nơi triển khai thật cho tới Phase 13; ưu tiên phương án miễn phí.
16. AI mặc định chạy Ollama trên host Windows (dùng GPU); gói free cloud chỉ bật khi chủ dự án tự đăng ký key miễn phí.

## 4. Rủi ro

| # | Rủi ro | Khả năng | Tác động | Giảm thiểu |
|---|---|---|---|---|
| R1 | Phạm vi rất lớn cho một người + AI | Cao | Cao | Cổng nghiệm thu từng phase, lát cắt dọc, hoãn hạng mục "C", không làm thừa |
| R2 | Docker chưa nhận PATH trong terminal hiện tại | Đã xảy ra | Thấp | Khởi động lại VS Code/terminal trước Phase 1 |
| R3 | Python 3.14 thiếu wheel (psycopg binary, argon2-cffi, ...) | Thấp–Trung bình | Trung bình | Kiểm tra ở Phase 1; chuyển về 3.13 bằng uv |
| R4 | Lỗi logic tài chính (chi hai lần, mất cập nhật) | Trung bình | Nghiêm trọng | Bất biến ledger, constraint DB, test đồng thời + property test, đối soát |
| R5 | Over-engineering (quá nhiều abstraction) | Trung bình | Trung bình | Domain thuần chỉ ở lõi tài chính; module khác làm thực dụng; ADR cho mọi abstraction mới |
| R6 | Model AI miễn phí nhỏ gọi tool kém ổn định, tiếng Việt chưa tốt | Cao | Trung bình | Router ít tool, schema đơn giản, fallback tất định, data card, eval chọn model; free cloud làm phương án thay thế |
| R7 | RAM 12 GB không đủ chạy cùng lúc Docker stack + Ollama + trình duyệt | Trung bình | Trung bình | Compose profile, giới hạn bộ nhớ WSL 2, dùng fake provider khi không cần AI thật, model 3–4B khi cần |
| R8 | Gói free cloud đổi điều khoản/giới hạn hoặc dùng dữ liệu để huấn luyện | Trung bình | Thấp | Mặc định local; dữ liệu là giả lập; adapter chung nên đổi provider dễ |
| R9 | Giới hạn Web Push (iOS cần PWA đã cài, HTTPS) | Trung bình | Thấp | Hướng dẫn cài đặt, localhost dùng được khi dev, TLS ở Phase 13 |
| R10 | Đặc thù Windows (line ending, file watching trong Docker) | Trung bình | Thấp | `.gitattributes`, backend WSL 2, watcher polling nếu cần |
| R11 | Tài liệu lệch khỏi code | Trung bình | Trung bình | Cập nhật tài liệu trong cổng chất lượng mỗi phase; OpenAPI sinh từ code |
| R12 | User tải giấy tờ tùy thân thật lên KYC mô phỏng | Thấp | Trung bình | Cảnh báo nổi bật, ảnh mẫu, giới hạn thời gian lưu file |

## 5. Câu hỏi còn mở

1. **GitHub**: CI có chạy trên remote GitHub hiện tại (`vnming/VieWallet`) không, và repo để public hay private? (Ảnh hưởng tới cách quản lý bí mật và việc trình bày portfolio.)

Câu hỏi đã được trả lời (2026-11-21): Docker đã cài; AI chỉ dùng model miễn phí; tài liệu viết bằng tiếng Việt.

## 6. Tài liệu đã tạo

| Tài liệu | Đường dẫn |
|---|---|
| Mục lục | [docs/README.md](README.md) |
| PRD (tầm nhìn, phạm vi, role, FR, NFR) | [product/prd.md](product/prd.md) |
| Luồng người dùng | [product/user-flows.md](product/user-flows.md) |
| Kiến trúc phần mềm | [architecture/architecture.md](architecture/architecture.md) |
| ERD + từ điển dữ liệu | [architecture/database.md](architecture/database.md) |
| Xử lý tài chính + vòng đời giao dịch | [architecture/financial-processing.md](architecture/financial-processing.md) |
| Payment Provider Simulator | [architecture/payment-provider-simulator.md](architecture/payment-provider-simulator.md) |
| Bảo mật, threat model, rủi ro, KYC | [architecture/security.md](architecture/security.md) |
| Trợ lý AI: kiến trúc, tool, an toàn | [architecture/ai-assistant.md](architecture/ai-assistant.md) |
| Thông báo & PWA | [architecture/notifications-pwa.md](architecture/notifications-pwa.md) |
| UI/UX + design system | [design/design-system.md](design/design-system.md) |
| Đặc tả API | [api/api-spec.md](api/api-spec.md) |
| Chiến lược kiểm thử, kế hoạch, test case chính | [quality/test-strategy.md](quality/test-strategy.md) |
| Roadmap + tiêu chí nghiệm thu | [roadmap.md](roadmap.md) |
| ADR | [adr/README.md](adr/README.md) |

Hoãn tới phase cài đặt (để phản ánh đúng code thật): CI/CD, deployment, hướng dẫn cài môi trường (Phase 1/13), release notes (Phase 13), OpenAPI chi tiết từng endpoint (sinh từ code).

## 7. Rà soát nhất quán đã thực hiện

- Bộ từ vựng trạng thái giống nhau giữa xử lý tài chính, database (`transactions.status`), mã lỗi API và component trạng thái trong design system.
- Quy tắc posting chỉ tham chiếu tài khoản có trong hệ thống tài khoản; cách dùng HOLD khớp state machine rút/chuyển/thanh toán và bất biến I5.
- Mọi endpoint di chuyển tiền trong đặc tả API đều đánh dấu `[I]` (idempotency) và ánh xạ tới một bước chuyển trong state machine.
- Mọi FR ưu tiên M đều có phase trong roadmap; test tài chính FT-01..FT-32 được phân cho phase 3–8.
- Danh mục tool AI chỉ có tool đọc và tool nháp; không tool nào ánh xạ tới API ghi.
- Mọi chỗ nhắc tới AI provider đã đổi sang model miễn phí (PRD FR-AI-07, NFR-16, kiến trúc, database `ai_requests`/`ai_model_configs`, API admin AI, test strategy, roadmap Phase 11, ADR-0009, threat model T18).
- Đã sửa trong lần rà soát đầu: mã kích hoạt kịch bản simulator khớp quy tắc bội 1.000 ₫; thứ tự khóa tài khoản hệ thống được làm rõ để tránh deadlock.

## 8. Phase 1 đề xuất — Project Foundation

Xem [Roadmap Phase 1](roadmap.md#phase-1--project-foundation). Tóm tắt: cấu trúc repo, lõi backend (config, DB/unit of work, lỗi, logging, Money, UUIDv7, idempotency store, outbox + khung worker), migration Alembic nền, frontend workspace với nền design system và khung hai app, Docker Compose stack, CI GitHub Actions, hạ tầng test. Không có tính năng nghiệp vụ. Điều kiện kết thúc: `docker compose up` chạy được, CI xanh, test nền đạt.
