# VieWallet — Tài liệu yêu cầu sản phẩm (PRD)

| Mục | Giá trị |
|---|---|
| Trạng thái | Bản nháp chờ duyệt (Phase 0) |
| Cập nhật | 2026-11-21 |
| Liên quan | [Kiến trúc](../architecture/architecture.md), [Xử lý tài chính](../architecture/financial-processing.md), [Roadmap](../roadmap.md) |

## 1. Tầm nhìn

VieWallet là ví điện tử fintech **mô phỏng**. Hệ thống hoạt động như một sản phẩm thật: đăng ký, KYC, ví có sổ cái (ledger) làm nguồn dữ liệu gốc, nạp tiền, rút tiền, chuyển tiền, thanh toán QR, hoàn tiền, đảo giao dịch, kiểm soát rủi ro, hệ thống vận hành (back office), push notification và trợ lý tài chính AI làm việc trên dữ liệu thật của backend thông qua các tool có kiểm soát.

Mục tiêu:

1. Dự án portfolio thể hiện kiến trúc chuyên nghiệp, tính đúng đắn tài chính, bảo mật và ứng dụng AI.
2. Môi trường học thiết kế ledger, state machine giao dịch, idempotency, concurrency, risk, audit và AI tool orchestration.
3. Sản phẩm demo được từ đầu đến cuối và mở rộng được về sau.

### 1.1 Ràng buộc bắt buộc

- Không ngân hàng thật, không tiền thật, không API thanh toán thật, không dữ liệu cá nhân hoặc tài chính thật.
- Mọi luồng tiền đi qua ledger nội bộ và **Payment Provider Simulator**.
- KYC là mô phỏng, không bao giờ được trình bày như xác minh danh tính thật.
- AI không chuyển tiền, không đổi số dư, không sửa bản ghi, không mở khóa ví, không đổi hạn mức, không phê duyệt bất kỳ việc gì.
- AI chỉ dùng **model miễn phí** (model local hoặc gói miễn phí). Không dùng API AI trả phí.

## 2. Người dùng và vai trò

| Role | Mô tả | Quyền điển hình |
|---|---|---|
| Customer | Người dùng ví | Hồ sơ, ví, giao dịch của chính mình, trợ lý AI |
| Merchant (mô phỏng) | Tài khoản loại khách hàng được đánh dấu là merchant; nhận thanh toán QR, hoàn tiền | Payment request của merchant, hoàn tiền cho thanh toán của mình |
| Support | Xử lý khiếu nại, xem dữ liệu khách hàng (đã che) | Xem user/giao dịch (đã che), quản lý ticket |
| Operations | Xử lý giao dịch treo/thất bại, sự cố provider | Truy vấn trạng thái provider, đề xuất hoàn tiền/đảo giao dịch |
| Finance | Đối soát, báo cáo tài chính, điều chỉnh | Đối soát, báo cáo, đề xuất và duyệt điều chỉnh |
| Risk Analyst | Xử lý cảnh báo rủi ro và giao dịch bị giữ | Duyệt/từ chối giao dịch bị giữ, khóa ví, đề xuất thay đổi risk rule |
| KYC Reviewer | Duyệt hồ sơ KYC | Duyệt/từ chối KYC |
| Admin | Vận hành nền tảng | Quản lý user, đề xuất cấu hình, gán role (không phải super) |
| Super Admin | Quyền cao nhất, quản lý role và permission | Mọi quyền, vẫn bị audit, vẫn phải qua maker-checker |

Role là dữ liệu (bảng RBAC), không phải hằng số trong code. Mọi permission được kiểm tra ở backend. Frontend chỉ ẩn những gì user không được làm.

## 3. Phạm vi

### 3.1 Trong phạm vi (toàn bộ sản phẩm, giao dần qua các phase)

**Ứng dụng người dùng** — đăng ký, đăng nhập/đăng xuất, phiên đăng nhập, đặt lại mật khẩu, hồ sơ, cài đặt bảo mật, PIN giao dịch, KYC mô phỏng, ví và số dư, lịch sử giao dịch (tìm kiếm, lọc, chi tiết, timeline trạng thái), nạp tiền, rút tiền, chuyển tiền nội bộ (số ví, tra cứu SĐT/email, QR), thanh toán QR cho merchant, yêu cầu thanh toán ("xin tiền"), người nhận đã lưu, hoàn tiền (phía merchant), thông báo (in-app + Web Push), tùy chọn thông báo, báo cáo tài chính, ngân sách, mục tiêu tiết kiệm, trợ lý AI, cảnh báo bảo mật, trợ giúp và hỗ trợ (khiếu nại), cài đặt PWA và xử lý offline.

**Ứng dụng quản trị** — app và đăng nhập riêng. Dashboard, user, chi tiết user, ví (khóa/mở khóa), giao dịch (chi tiết, timeline, thông tin provider, giao dịch liên quan, thông tin rủi ro), các màn hình nạp/rút/chuyển/thanh toán, hoàn tiền, đảo giao dịch, điều chỉnh (maker-checker), hàng đợi phê duyệt, duyệt KYC, cảnh báo và rule rủi ro, khiếu nại, đối soát, cấu hình phí và hạn mức, điều khiển simulator, quản lý thông báo, quản lý AI và mức sử dụng, báo cáo tài chính và hệ thống, audit log, role và permission, hoạt động của admin, sức khỏe hệ thống.

**Payment Provider Simulator** — service riêng mô phỏng ngân hàng/cổng thanh toán bên ngoài, có kịch bản cấu hình được.

**AI** — trợ lý tài chính cho khách hàng, trợ lý phân tích cho nhân viên, tool registry, guardrails, theo dõi mức sử dụng.

### 3.2 Ngoài phạm vi

- Tiền thật, tích hợp ngân hàng hoặc thẻ thật, eKYC thật (OCR/so khớp khuôn mặt), SMS thật.
- App native (PWA phục vụ mobile).
- Nhiều ví cho một user trên UI (schema hỗ trợ; UI chỉ hiển thị một ví chính).
- Đa tiền tệ trên UI (schema và mô hình tiền hỗ trợ; chỉ bật VND).
- Tín dụng, cho vay, lãi suất, đầu tư, crypto.
- Microservices.
- API AI trả phí.

## 4. Yêu cầu chức năng

ID được tham chiếu từ test và roadmap. Độ ưu tiên: **M** = bắt buộc (MVP của phase), **S** = nên có, **C** = có thể có.

### 4.1 Định danh và truy cập (FR-ID)

| ID | Yêu cầu | Ưu tiên |
|---|---|---|
| FR-ID-01 | Đăng ký bằng email, mật khẩu, họ tên, SĐT (tùy chọn). Xác minh email bằng mã/link dùng một lần (gửi về hộp thư dev). | M |
| FR-ID-02 | Đăng nhập bằng email + mật khẩu. Thông báo lỗi chung chung khi thất bại (không lộ email có tồn tại). | M |
| FR-ID-03 | Access token ngắn hạn, refresh token xoay vòng trong cookie httpOnly, bản ghi session ở server. | M |
| FR-ID-04 | Đăng xuất (phiên hiện tại) và đăng xuất mọi phiên; liệt kê phiên đang hoạt động kèm thiết bị và lần hoạt động cuối; thu hồi từng phiên. | M |
| FR-ID-05 | Giới hạn tần suất đăng nhập theo IP và theo tài khoản; khóa tăng dần khi sai nhiều lần; gửi thông báo bảo mật khi bị khóa. | M |
| FR-ID-06 | Đặt lại mật khẩu bằng token qua email (dùng một lần, hết hạn sau 30 phút); thu hồi mọi phiên sau khi đặt lại. | M |
| FR-ID-07 | Đổi mật khẩu (cần mật khẩu hiện tại); đổi email (cần mật khẩu + xác minh email mới). | M |
| FR-ID-08 | Tài khoản nhân viên phải đăng ký TOTP MFA trước khi vào app quản trị. | M |
| FR-ID-09 | Lịch sử đăng nhập (thời gian, IP, user agent, kết quả) cho user và nhân viên xem. | M |
| FR-ID-10 | RBAC: role, permission, role-permission, user-role; kiểm tra permission ở mọi endpoint quản trị và endpoint nhạy cảm. | M |
| FR-ID-11 | Trạng thái tài khoản: active, locked (tạm thời), restricted, suspended, closed. | M |

### 4.2 PIN giao dịch (FR-PIN)

| ID | Yêu cầu | Ưu tiên |
|---|---|---|
| FR-PIN-01 | User đặt PIN 6 số sau lần đăng nhập đầu; mọi thao tác trừ tiền bị chặn khi chưa có PIN. | M |
| FR-PIN-02 | PIN lưu dạng hash Argon2id kèm pepper phía server; không bao giờ ghi log hay trả về. | M |
| FR-PIN-03 | Sai PIN 5 lần liên tiếp thì khóa PIN 30 phút; khóa lặp lại thì tăng mức (xem tài liệu Bảo mật). Gửi thông báo bảo mật khi khóa. | M |
| FR-PIN-04 | Đổi PIN (cần PIN cũ). Đặt lại PIN (mật khẩu + mã email). Chặn rút tiền lớn trong 24 giờ sau khi đặt lại PIN. | M |
| FR-PIN-05 | Từ chối PIN yếu (cùng một số, dãy đơn giản, giống ngày sinh trong hồ sơ). | S |

### 4.3 KYC mô phỏng (FR-KYC)

| ID | Yêu cầu | Ưu tiên |
|---|---|---|
| FR-KYC-01 | User nộp thông tin cá nhân giả lập và ảnh giấy tờ giả lập (CCCD, hộ chiếu, bằng lái). UI cảnh báo: không tải giấy tờ thật. | M |
| FR-KYC-02 | Vòng đời: NOT_STARTED → PENDING → IN_REVIEW → APPROVED / REJECTED; APPROVED → EXPIRED; REJECTED → PENDING (nộp lại). | M |
| FR-KYC-03 | Cấp KYC quyết định hạn mức (tier 0 chưa xác minh, tier 1 đã xác minh). Rút tiền cần tier 1. | M |
| FR-KYC-04 | Hàng đợi cho reviewer, nhận hồ sơ, duyệt/từ chối kèm mã lý do; lịch sử xác minh đầy đủ; audit. | M |
| FR-KYC-05 | Tùy chọn quyết định tự động giả lập (theo kịch bản) để demo nhanh. | C |

### 4.4 Ví và số dư (FR-WAL)

| ID | Yêu cầu | Ưu tiên |
|---|---|---|
| FR-WAL-01 | Mỗi user một ví chính cho mỗi loại tiền, tạo khi đăng ký. Số ví dễ đọc (10 chữ số, có chữ số kiểm tra). | M |
| FR-WAL-02 | Trạng thái ví: ACTIVE, FROZEN (không ghi nợ/ghi có trừ đảo giao dịch được phép), SUSPENDED (admin khóa, chặn mọi thao tác), CLOSED (kết thúc, số dư phải bằng 0). | M |
| FR-WAL-03 | Hiển thị số dư khả dụng, số tiền đang giữ (rút tiền đang chờ / đang review) và lịch sử số dư. Mọi giá trị lấy từ balance projection của ledger. | M |
| FR-WAL-04 | Số dư ví khách hàng không bao giờ âm (ứng dụng và constraint DB cùng đảm bảo). | M |
| FR-WAL-05 | Risk/Admin khóa/mở khóa ví kèm lý do; khách hàng tự khóa ví ("khóa ví của tôi") và mở lại bằng mật khẩu + PIN. | M |

### 4.5 Giao dịch (FR-TXN)

| ID | Yêu cầu | Ưu tiên |
|---|---|---|
| FR-TXN-01 | Loại: DEPOSIT, WITHDRAWAL, TRANSFER, PAYMENT, REFUND, REVERSAL, ADJUSTMENT. | M |
| FR-TXN-02 | Mỗi giao dịch có id, mã tham chiếu công khai, loại, số tiền, phí, tiền tệ, các bên, trạng thái, thời điểm, mô tả, metadata, idempotency key, giao dịch liên quan, thông tin provider, liên kết journal. | M |
| FR-TXN-03 | State machine riêng cho từng loại; từ chối chuyển trạng thái không hợp lệ; mọi lần chuyển được ghi vào event log chỉ-thêm (append-only). | M |
| FR-TXN-04 | Mọi POST làm di chuyển tiền bắt buộc có `Idempotency-Key`. Gửi lại trả kết quả cũ; cùng key nhưng payload khác bị từ chối. | M |
| FR-TXN-05 | Lịch sử phân trang cursor, lọc (loại, trạng thái, chiều tiền, khoảng ngày, khoảng số tiền, đối tác), tìm theo mã tham chiếu/mô tả. | M |
| FR-TXN-06 | Trang chi tiết có timeline trạng thái, chi tiết phí, đối tác (đã che), giao dịch liên quan, nút hỗ trợ ("báo lỗi giao dịch"). | M |
| FR-TXN-07 | Phân loại (danh mục hệ thống theo loại/danh mục merchant; user được sửa). | S |
| FR-TXN-08 | Xuất CSV lịch sử theo khoảng ngày (backend tạo file). | S |

### 4.6 Nạp tiền (FR-DEP)

| ID | Yêu cầu | Ưu tiên |
|---|---|---|
| FR-DEP-01 | User tạo lệnh nạp với số tiền và phương thức (chuyển khoản giả lập, thẻ giả lập). | M |
| FR-DEP-02 | Backend tạo charge ở provider; user được chuyển sang trang "SimBank" của simulator để xác nhận, hoặc kịch bản tự xử lý. | M |
| FR-DEP-03 | Chỉ ghi có vào ví sau khi có xác nhận đã kiểm chứng từ provider (callback hoặc truy vấn trạng thái). | M |
| FR-DEP-04 | Xử lý thất bại, đang chờ, timeout, callback trùng, callback sai, provider không khả dụng. | M |
| FR-DEP-05 | Số tiền tối thiểu/tối đa và hạn mức theo tier KYC. | M |

### 4.7 Rút tiền (FR-WDR)

| ID | Yêu cầu | Ưu tiên |
|---|---|---|
| FR-WDR-01 | User rút về tài khoản ngân hàng giả lập đã lưu. Cần KYC tier 1, PIN, idempotency key. | M |
| FR-WDR-02 | Số tiền + phí được chuyển ngay vào tài khoản giữ tiền (HOLD); lệnh chi (payout) được gửi sang provider. | M |
| FR-WDR-03 | Thành công: tất toán khoản giữ. Thất bại: hoàn khoản giữ về ví. Chưa rõ kết quả: truy vấn trạng thái, không bao giờ gửi lại mù quáng. | M |
| FR-WDR-04 | Rút tiền lớn hoặc rủi ro được review thủ công trước khi gửi đi. | M |
| FR-WDR-05 | Nếu provider trả lại payout đã hoàn tất thì tạo REVERSAL ghi có lại vào ví. | S |

### 4.8 Chuyển tiền (FR-TRF)

| ID | Yêu cầu | Ưu tiên |
|---|---|---|
| FR-TRF-01 | Chuyển nội bộ theo số ví, tra cứu SĐT/email đã xác minh, hoặc quét QR cá nhân. | M |
| FR-TRF-02 | Hai bước: báo giá (tên người nhận đã che, số tiền, phí, tổng, hết hạn sau 10 phút) → xác nhận bằng PIN. | M |
| FR-TRF-03 | Chặn khi: không đủ số dư, ví một trong hai bên bị khóa/đình chỉ, tài khoản bị hạn chế, thiếu/sai PIN, vượt hạn mức, risk BLOCK, tự chuyển cho mình, khác tiền tệ. | M |
| FR-TRF-04 | Ghi nợ và ghi có diễn ra nguyên tử trong một DB transaction, với một journal cân bằng. | M |
| FR-TRF-05 | Risk REVIEW giữ tiền lại; Risk Analyst duyệt (hoàn tất) hoặc từ chối (hoàn lại). | M |
| FR-TRF-06 | Danh bạ người nhận có biệt danh; người nhận gần đây. | S |
| FR-TRF-07 | Chỉ nhân viên được đảo giao dịch, qua maker-checker, và chỉ khi người nhận đủ số dư khả dụng; nếu không thì mở hồ sơ thu hồi. | M |

### 4.9 Thanh toán và QR (FR-PAY)

| ID | Yêu cầu | Ưu tiên |
|---|---|---|
| FR-PAY-01 | Merchant tạo payment request (số tiền cố định hoặc để trống, mô tả, hạn) hiển thị dạng mã QR chứa payload có chữ ký. | M |
| FR-PAY-02 | Khách quét/mở QR, xem tên merchant đã xác minh và số tiền, xác nhận bằng PIN. | M |
| FR-PAY-03 | Trạng thái payment request: OPEN → PAID / EXPIRED / CANCELLED; dùng một lần hoặc nhiều lần. | M |
| FR-PAY-04 | "Xin tiền" giữa người dùng (một khách yêu cầu khách khác trả một khoản, người kia trả hoặc từ chối). | S |
| FR-PAY-05 | Merchant hoàn tiền (toàn phần hoặc từng phần, nhiều lần nhưng không vượt số tiền gốc); hoàn tiền giá trị lớn cần nhân viên duyệt. | M |
| FR-PAY-06 | Mã danh mục merchant (MCC) quyết định danh mục chi tiêu trong analytics. | S |

### 4.10 Phí và hạn mức (FR-FEE, FR-LIM)

| ID | Yêu cầu | Ưu tiên |
|---|---|---|
| FR-FEE-01 | Fee rule theo loại giao dịch, tiền tệ, tier KYC: cố định + phần trăm (basis point), tối thiểu, tối đa, thời gian hiệu lực. Mặc định MVP: phí 0, riêng phí rút tiền demo cấu hình được. | M |
| FR-FEE-02 | Backend tính phí lúc báo giá; phiên bản fee rule được lưu trên giao dịch. | M |
| FR-LIM-01 | Limit rule: tối đa/tối thiểu mỗi giao dịch, tổng tiền và số lần theo ngày và tháng, theo loại, theo tier KYC, ghi đè theo user, giảm theo rủi ro. | M |
| FR-LIM-02 | Hạn mức được kiểm tra khi đang giữ khóa ví, nên các request đồng thời không thể vượt qua. | M |
| FR-LIM-03 | User xem hạn mức còn lại theo ngày/tháng. | S |

### 4.11 Rủi ro (FR-RSK)

| ID | Yêu cầu | Ưu tiên |
|---|---|---|
| FR-RSK-01 | Rule engine đánh giá mọi giao dịch trừ tiền; trả về điểm, các rule khớp, quyết định ALLOW / REVIEW / BLOCK. Quyết định được lưu. | M |
| FR-RSK-02 | Rule: tần suất (velocity), số tiền tăng đột biến so với lịch sử, người nhận mới + số tiền lớn, thời gian chờ sau khi đổi thông tin đăng nhập, thất bại lặp lại, phát hiện trùng lặp, cờ rủi ro tài khoản, tier KYC. | M |
| FR-RSK-03 | Hàng đợi cảnh báo cho analyst; thao tác: duyệt giao dịch bị giữ, từ chối, khóa ví, gắn cờ tài khoản, thêm ghi chú. | M |
| FR-RSK-04 | Cấu hình rule có phiên bản; thay đổi phải qua maker-checker. | S |

### 4.12 Thông báo (FR-NTF)

| ID | Yêu cầu | Ưu tiên |
|---|---|---|
| FR-NTF-01 | Hộp thư in-app có danh mục, đã đọc/chưa đọc, đánh dấu đã đọc tất cả. | M |
| FR-NTF-02 | Web Push qua VAPID tới thiết bị đã đăng ký; quản lý đăng ký (liệt kê/xóa thiết bị). | M |
| FR-NTF-03 | Tùy chọn theo danh mục và kênh; thông báo bảo mật và thông báo giao dịch bắt buộc không tắt hoàn toàn được trong app. | M |
| FR-NTF-04 | Theo dõi gửi theo từng kênh, có retry và trạng thái; dọn đăng ký push đã chết. | M |
| FR-NTF-05 | Kênh email tới hộp thư dev cho xác minh, đặt lại mật khẩu và cảnh báo bảo mật. | M |
| FR-NTF-06 | Admin gửi thông báo hệ thống (broadcast) có lọc đối tượng, qua maker-checker. | S |

### 4.13 Báo cáo và phân tích (FR-RPT)

| ID | Yêu cầu | Ưu tiên |
|---|---|---|
| FR-RPT-01 | Thu/chi theo tháng, chi tiêu theo ngày, chi tiêu theo danh mục, số giao dịch, giao dịch lớn nhất, lịch sử số dư, chọn khoảng ngày. | M |
| FR-RPT-02 | Ngân sách theo danh mục theo tháng, cảnh báo khi chạm ngưỡng (rule-based). | S |
| FR-RPT-03 | Mục tiêu tiết kiệm (mục tiêu, hạn, tiến độ là con số đánh dấu, không di chuyển tiền). | S |
| FR-RPT-04 | Phát hiện chi tiêu định kỳ (cùng đối tác, chu kỳ đều). | C |
| FR-RPT-05 | Báo cáo admin: khối lượng theo loại/ngày, tỷ lệ thành công/thất bại, doanh thu phí, hiệu năng provider, phễu KYC. | M |

### 4.14 Trợ lý AI (FR-AI)

| ID | Yêu cầu | Ưu tiên |
|---|---|---|
| FR-AI-01 | Giao diện chat có trả lời dạng streaming, danh sách hội thoại, xóa hội thoại. | M |
| FR-AI-02 | Câu trả lời dựa trên tool chỉ-đọc, giới hạn trong dữ liệu của user đã xác thực. Số liệu tài chính trên UI lấy từ kết quả tool ("data card"), không lấy từ chữ model viết. | M |
| FR-AI-03 | Giải thích giao dịch và trạng thái, phân tích chi tiêu, tóm tắt tháng, gợi ý ngân sách và tiết kiệm, giải thích hoạt động bất thường, hướng dẫn sử dụng qua tìm kiếm help center. | M |
| FR-AI-04 | Có thể đề xuất *bản nháp* thao tác (ví dụ form chuyển tiền điền sẵn). User tự hoàn tất trên UI thường, có PIN. AI không bao giờ tự thực hiện. | S |
| FR-AI-05 | Trợ lý cho nhân viên với tool phân tích chỉ-đọc phạm vi admin, kiểm soát bằng permission. | S |
| FR-AI-06 | Log mức sử dụng (token, độ trễ, tool, lỗi), quota theo user, công tắc tắt khẩn cấp, quản lý phiên bản prompt, cấu hình model trong admin. | M |
| FR-AI-07 | Chỉ dùng model miễn phí: model local (Ollama) là mặc định, gói miễn phí của dịch vụ cloud là tùy chọn, và provider giả lập tất định (fake) cho test/demo. | M |

### 4.15 Vận hành, hỗ trợ và audit (FR-OPS)

| ID | Yêu cầu | Ưu tiên |
|---|---|---|
| FR-OPS-01 | Hàng đợi vận hành: giao dịch treo quá SLA, thao tác provider thất bại, kết quả chưa rõ, yêu cầu hoàn tiền/đảo giao dịch, mục đối soát, khiếu nại. | M |
| FR-OPS-02 | Maker-checker cho điều chỉnh, hoàn tiền giá trị lớn, đảo giao dịch, mở khóa ví sau khi risk khóa, thay đổi hạn mức/phí/rule/cấu hình, gán role. | M |
| FR-OPS-03 | Đối soát: nội bộ (ledger so với balance projection) và bên ngoài (sao kê provider so với dữ liệu nội bộ), mục chênh lệch có quy trình xử lý. | M |
| FR-OPS-04 | Khiếu nại/ticket gắn với user và giao dịch; trạng thái; ghi chú nội bộ; phản hồi cho khách. | M |
| FR-OPS-05 | Audit log cho mọi thao tác của nhân viên và thao tác nhạy cảm của khách; append-only; tìm kiếm được; xuất được. | M |
| FR-OPS-06 | Trang sức khỏe hệ thống: DB, heartbeat worker, độ trễ outbox, tình trạng provider, thống kê gửi push, trạng thái AI provider. | S |

## 5. Yêu cầu phi chức năng

| ID | Nhóm | Yêu cầu |
|---|---|---|
| NFR-01 | Tính đúng | Bất biến ledger luôn đúng: mọi journal cân bằng theo tiền tệ; balance projection bằng tổng posting; số dư khách hàng không âm. Kiểm chứng bằng test và job đối soát. |
| NFR-02 | Nhất quán | Mọi thay đổi trạng thái tài chính diễn ra trong một DB transaction ACID cùng với outbox event và audit row. |
| NFR-03 | Idempotency | Mọi endpoint di chuyển tiền và mọi callback provider đều idempotent. |
| NFR-04 | Concurrency | 50 lệnh chuyển đồng thời từ một ví không bao giờ làm âm ví và không mất cập nhật (có test). |
| NFR-05 | Bảo mật | Tham chiếu OWASP ASVS L2; biện pháp cho OWASP Top 10 và OWASP API Top 10 được ghi trong threat model. |
| NFR-06 | Riêng tư | PII mặc định bị che trong màn hình nhân viên; xem bản đầy đủ cần permission và bị audit. Không có PII trong log hay prompt AI ngoài những gì tool cần. |
| NFR-07 | Hiệu năng | p95 latency API < 300 ms cho đọc và < 500 ms cho chuyển nội bộ trên laptop dev với seed data (100k giao dịch). |
| NFR-08 | Sẵn sàng | Simulator sập không làm hỏng trạng thái; nạp/rút suy giảm có kiểm soát, thông báo rõ cho user. |
| NFR-09 | Quan sát | Log JSON có cấu trúc kèm correlation ID; endpoint metrics; endpoint health/readiness. |
| NFR-10 | Tiếp cận | WCAG 2.2 AA cho cả hai app; điều hướng bằng bàn phím; hỗ trợ giảm chuyển động. |
| NFR-11 | i18n | Tiếng Việt mặc định, tiếng Anh thứ hai; mọi chuỗi trong file dịch; định dạng VND qua `Intl`. |
| NFR-12 | Bảo trì | Modular monolith có ranh giới module được kiểm tra tự động; type checking (mypy, tsc) và lint trong CI. |
| NFR-13 | Kiểm thử | Lõi tài chính ≥ 90 % branch coverage; toàn backend ≥ 80 %; mọi bug fix có regression test. |
| NFR-14 | Di động | Toàn bộ stack chạy bằng `docker compose up`; phát triển local không phụ thuộc cloud. |
| NFR-15 | Thời gian | Mọi timestamp lưu UTC; ngày làm việc, hạn mức ngày và báo cáo dùng `Asia/Ho_Chi_Minh`. |
| NFR-16 | Chi phí | Không có thành phần nào phát sinh phí; AI chạy được hoàn toàn local. |

## 6. Giả định

Ghi trong [Báo cáo Phase 0 §3](../00-phase0-report.md#3-giả-định). Chính: chỉ VND (exponent 0), mỗi user một ví chính, email là kênh OTP duy nhất (hộp thư dev), merchant là tài khoản mô phỏng, AI mặc định chạy model local miễn phí.

## 7. Chỉ số thành công (portfolio/demo)

- Kịch bản demo E2E chạy xanh trên Playwright: đăng ký → KYC → nạp tiền → chuyển tiền → thanh toán QR → hoàn tiền → AI tóm tắt → admin đối soát.
- Kiểm tra bất biến ledger đúng sau property-based test ngẫu nhiên 10.000 thao tác.
- Không có phát hiện mức critical trong security review ở Phase 12.
