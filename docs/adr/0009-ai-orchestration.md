# ADR-0009: Tự chạy vòng lặp tool AI sau provider port; chỉ model miễn phí; tool chỉ-đọc

- Trạng thái: Accepted (2026-11-21)
- Ngày: 2026-11-21

## Bối cảnh

Trợ lý phải dùng dữ liệu backend theo phạm vi của user, có kiểm tra permission, audit và che dữ liệu. Chủ dự án yêu cầu **chỉ dùng model miễn phí**. Trợ lý không bao giờ được thao tác với tiền.

Máy dev: RTX 4050 Laptop 6 GB VRAM, RAM 12 GB — chạy được model mở cỡ ~3–8B lượng tử hóa 4-bit qua Ollama.

## Quyết định

- VieWallet tự chạy vòng lặp orchestration (tool calling kiểu chat completions) trong module `ai`, vì tool cần DB session, ngữ cảnh xác thực và audit của hệ thống, và provider phải thay được.
- Port `LLMProvider` với hai adapter:
  - **OpenAI-compatible** (HTTP `/v1/chat/completions` có `tools`): phục vụ Ollama local (mặc định), LM Studio/llama.cpp, và gói free cloud có endpoint tương thích (tùy chọn, chủ dự án tự bật bằng key miễn phí trong `.env`).
  - **Fake** tất định: CI, test, demo dự phòng.
- Không tích hợp API AI trả phí. Không có trường chi phí trong dữ liệu sử dụng.
- Bù cho model nhỏ: router chọn nhóm tool nhỏ, schema đơn giản, sửa lỗi tool call một lần rồi fallback tất định, nhiệt độ thấp, không dựa vào tính năng riêng của nhà cung cấp.
- Registry chỉ có tool đọc và tool "nháp" (chỉ trả link tới form điền sẵn trên UI). Không tool nào thay đổi trạng thái tài chính.
- Số liệu tài chính trên UI lấy từ kết quả tool (data card). Số trong văn bản model được đối chiếu với output của tool.
- Model local cụ thể được chọn ở Phase 11 bằng bộ eval tiếng Việt chạy trên máy dev.

## Hệ quả

- Không phát sinh chi phí; dữ liệu mặc định không rời máy.
- Chất lượng hội thoại thấp hơn model thương mại lớn; tính đúng của số liệu vẫn được đảm bảo nhờ data card và kiểm tra output.
- Ollama chiếm RAM/VRAM; khi dev cần tắt bớt service hoặc dùng fake provider. Chạy Ollama trên host Windows để dùng GPU dễ hơn trong Docker.
- Hành vi model khác nhau, nên bộ eval phải chạy cho từng model ứng viên.
