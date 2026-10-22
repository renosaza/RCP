# Local model profile

Download the weights separately; never put them in Git. The pinned GGUF selected for this deployment is:

- Repository: `unsloth/gemma-4-12b-it-GGUF`
- Revision: `fc034cfff751157913579611efad8462ac1be606`
- File: `gemma-4-12b-it-Q4_K_M.gguf`
- Bytes: `7121861440`
- SHA-256: `0a270ec9fe6b34f4a0d33992b6135117b484ebc4766ab76b51d4ae8c457e4c42`

Verify the hash before loading. Use a llama.cpp build with Gemma 4 support and compatible GPU binaries. Example (replace paths):

```sh
/path/to/llama-server \
  --model /path/to/gemma-4-12b-it-Q4_K_M.gguf \
  --alias rcp-coder --host 127.0.0.1 --port 8081 \
  --ctx-size 16384 --n-gpu-layers 99 --parallel 1 \
  --threads 8 --threads-batch 10 --batch-size 512 --ubatch-size 128 \
  --flash-attn off --cache-ram 0 --no-webui \
  --reasoning-budget 0 --chat-template-kwargs '{"enable_thinking":false}'
```

This is a text-only profile, with no multimodal projector. Resource requirements depend on the build and hardware. Leave RAM and VRAM headroom; do not assume that the file size equals total runtime memory. Keep the previous model and service config for rollback.
