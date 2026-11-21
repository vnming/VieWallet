# Payment Provider Simulator

| Mục | Giá trị |
|---|---|
| Trạng thái | Bản nháp chờ duyệt (Phase 0) |
| Cập nhật | 2026-11-21 |
| Liên quan | [Xử lý tài chính](financial-processing.md), [ADR-0005](../adr/0005-simulator-as-separate-service.md) |

## 1. Mục đích

Đóng vai ngân hàng / cổng thanh toán bên ngoài để phần tích hợp provider của VieWallet được chạy thật: lời gọi mạng, timeout, callback bất đồng bộ có chữ ký, callback trùng và callback sai, sao kê để đối soát. Đây là **FastAPI service riêng** với schema, bí mật và giao diện nhỏ riêng ("SimBank"). Simulator không bao giờ chạm vào bảng của VieWallet.

## 2. Provider abstraction (trong backend VieWallet)

```python
class PaymentProvider(Protocol):
    code: str
    async def create_charge(self, req: ChargeRequest) -> ChargeResult: ...      # nạp tiền
    async def create_payout(self, req: PayoutRequest) -> PayoutResult: ...      # rút tiền
    async def get_operation(self, operation_key: str) -> OperationStatus: ...   # truy vấn trạng thái
    async def refund_charge(self, req: ChargeRefundRequest) -> RefundResult: ...  # dự phòng (mô phỏng chargeback)
    def verify_callback(self, headers: Mapping[str, str], raw_body: bytes) -> ProviderEvent: ...
    async def fetch_statement(self, business_date: date) -> Statement: ...
```

- Request mang `operation_key` (= `provider_operations.id`), số tiền (đơn vị nhỏ nhất), tiền tệ, mã tham chiếu của ta, callback URL.
- Kết quả được chuẩn hóa thành `ACCEPTED | SUCCEEDED | FAILED | PENDING` kèm `provider_reference` và mã lỗi. Lỗi truyền tải ném `ProviderTimeout` / `ProviderUnavailable` (bên gọi ánh xạ sang `UNKNOWN` hoặc lỗi có thể thử lại — không bao giờ ánh xạ thành FAILED với payout).
- `SimulatorProvider` cài đặt bằng `httpx.AsyncClient` (connect timeout 3 giây, read timeout 10 giây, không tự retry — retry là quyết định nghiệp vụ).
- Provider thật sau này = adapter mới + cấu hình. Code `transactions` không đổi.

## 3. API của simulator

Xác thực: `Authorization: Bearer <merchant API key>` từ VieWallet (bí mật theo môi trường). Callback ký bằng webhook secret riêng.

| Method | Path | Mục đích |
|---|---|---|
| POST | `/v1/charges` | Tạo charge. Body: `operation_key, amount, currency, reference, method, callback_url, return_url`. Trả `provider_reference, status, checkout_url`. Idempotent theo `operation_key` (cùng body ⇒ cùng response; khác body ⇒ 409). |
| POST | `/v1/payouts` | Tạo payout tới tài khoản ngân hàng giả lập. Idempotent theo `operation_key`. |
| GET | `/v1/operations/{operation_key}` | Truy vấn trạng thái. |
| POST | `/v1/charges/{ref}/refunds` | Mô phỏng hoàn tiền/chargeback. |
| GET | `/v1/statements/{date}` | Sao kê ngày (JSON lines hoặc CSV). |
| GET | `/checkout/{ref}` | Trang SimBank: hiển thị số tiền và form đăng nhập ngân hàng giả; nút Duyệt / Từ chối / Đóng. |
| GET | `/health` | Sức khỏe. |

Callback tới VieWallet: `POST {callback_url}` với body `{event_id, type: "charge.succeeded"|"charge.failed"|"charge.expired"|"payout.succeeded"|"payout.failed"|"payout.returned", provider_reference, operation_key, amount, currency, occurred_at}` và header `X-Sim-Timestamp`, `X-Sim-Signature: v1=<hex HMAC-SHA256(secret, timestamp + "." + raw_body)>`. VieWallet từ chối timestamp cũ hơn 5 phút (chống phát lại) và so chữ ký trong thời gian hằng (constant time). Simulator gửi lại callback khi nhận mã khác 2xx: 5 lần với backoff (10 giây, 30 giây, 2 phút, 10 phút, 1 giờ).

## 4. Kịch bản

Kịch bản quyết định một thao tác diễn ra thế nào. Thứ tự áp dụng: **ghi đè theo thao tác** (admin đặt "thao tác kế tiếp của user X") → **kích hoạt theo số tiền** → **mặc định toàn cục**.

Kích hoạt theo số tiền (giống thẻ test; chỉ dev/simulation, có ghi trên UI). Nạp và rút là bội của 1.000 ₫, nên mã kích hoạt là `t = (amount / 1000) mod 1000`. Ví dụ: 1.111.000 ₫ ⇒ `t = 111`; 2.500.000 ₫ ⇒ `t = 500` (bình thường).

| Mã `t` | Kịch bản |
|---|---|
| giá trị khác | Thành công (charge qua trang checkout, payout tự thành công sau 3 giây) |
| `111` | Thất bại ngay (`INSUFFICIENT_FUNDS` / `ACCOUNT_CLOSED`) |
| `222` | Giữ trạng thái chờ, hoàn tất sau 2 phút |
| `333` | Provider timeout: chấp nhận nhưng trả HTTP response chậm 30 giây (VieWallet timeout ở 10 giây ⇒ UNKNOWN), sau đó thao tác thành công |
| `444` | Lỗi mạng trước khi được chấp nhận (connection reset), không tạo gì |
| `555` | Callback trùng (gửi 3 lần, thêm một lần sau 1 phút) |
| `666` | Callback đầu sai chữ ký, callback đúng sau 1 phút |
| `777` | Provider không khả dụng (HTTP 503) |
| `888` | Thành công, nhưng sao kê báo số tiền khác (chênh lệch đối soát) |
| `999` | Payout thành công, sau 5 phút bị trả lại (`payout.returned`) |

Các điều khiển khác (bảng admin trong VieWallet, permission `simulator:manage`, chỉ khi `ENVIRONMENT in {development, simulation}`):
- Chế độ toàn cục: NORMAL, DEGRADED (ngẫu nhiên 20 % thất bại, độ trễ 1–5 giây), OUTAGE (mọi request trả 503).
- Chèn độ trễ, trễ callback, bỏ callback, callback sai thứ tự (failed đến sau succeeded).
- Đồng thời: simulator xử lý request song song và an toàn khi nhiều request cùng `operation_key` (unique constraint + response idempotent).
- Thao tác tay trên một operation: ép thành công / thất bại / hết hạn / trả lại, gửi lại callback.

## 5. Mô hình dữ liệu simulator (schema `simulator`)

`sim_operations(operation_key UNIQUE, kind, amount, currency, reference, status, scenario, provider_reference UNIQUE, callback_url, created_at, resolved_at, request_hash)`, `sim_callbacks(event_id UNIQUE, operation_key, type, attempt, status, sent_at, response_code)`, `sim_settings(key, value)`, `sim_bank_accounts` (hư cấu), `sim_logs` (log request/response cho màn hình giám sát).

## 6. Giám sát

- Simulator cung cấp `/v1/admin/logs` và `/v1/admin/operations` (bảo vệ bằng API key); app admin VieWallet hiển thị trong mục "Provider Simulator".
- Phía VieWallet: histogram độ trễ provider, số lượng thành công/thất bại/không rõ, số callback sai chữ ký, tồn đọng truy vấn trạng thái — trên trang Sức khỏe hệ thống.

## 7. Bảo mật simulator

- Môi trường production-like không mở simulator ra ngoài, trừ trang checkout và đường gửi callback ra.
- Endpoint điều khiển cần admin API key riêng và bị tắt khi `SIM_ENABLE_CONTROLS=false`.
- Bí mật qua biến môi trường; khác nhau theo môi trường; không bao giờ commit.
