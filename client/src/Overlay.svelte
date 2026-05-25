<script>
  import { onMount } from 'svelte';
  import { listen } from '@tauri-apps/api/event';

  // 録音中インジケータ (hotkey 押下中)
  let recording = $state(false);
  // 最新の 1 セグメントだけを保持。
  // 仕様 §5.2: 同一 segment_id の partial は前を**置換**する。
  let segment = $state(null); // { id, kind: 'partial'|'final'|'formatted', text }

  onMount(async () => {
    const unlistens = [];

    unlistens.push(
      await listen('recording-status', (e) => {
        recording = e.payload?.active === true;
      })
    );

    unlistens.push(
      await listen('segment-update', (e) => {
        const p = e.payload ?? {};
        segment = { id: p.segment_id, kind: p.kind, text: p.text };
      })
    );

    unlistens.push(
      await listen('segment-end', () => {
        recording = false;
      })
    );

    return () => unlistens.forEach((u) => u());
  });
</script>

<div class="overlay">
  <div class="indicator">
    {#if recording}
      <span class="rec-dot"></span>
      <span class="rec-label">REC</span>
    {:else}
      <span class="rec-dot-off"></span>
      <span class="rec-label idle">—</span>
    {/if}
  </div>
  <div class="text">
    {#if segment}
      <span class="seg {segment.kind ?? 'idle'}">{segment.text}</span>
    {:else}
      <span class="placeholder">{recording ? '聞いています...' : ''}</span>
    {/if}
  </div>
</div>

<style>
  :global(html), :global(body) {
    margin: 0;
    padding: 0;
    background: transparent;
    overflow: hidden;
  }
  :global(body) {
    font-family: -apple-system, system-ui, "Hiragino Sans", sans-serif;
  }
  .overlay {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 16px 20px;
    background: rgba(28, 28, 30, 0.92);
    color: #fff;
    border-radius: 14px;
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.4);
    -webkit-backdrop-filter: blur(8px);
    backdrop-filter: blur(8px);
    min-height: 32px;
    user-select: none;
  }
  .indicator {
    display: flex;
    flex-direction: column;
    align-items: center;
    min-width: 38px;
  }
  .rec-dot {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: #ff3b30;
    box-shadow: 0 0 8px rgba(255, 59, 48, 0.6);
    animation: pulse 1.2s ease-in-out infinite;
  }
  .rec-dot-off {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: #555;
  }
  .rec-label {
    margin-top: 4px;
    font-size: 10px;
    letter-spacing: 1px;
    color: #ff7060;
    font-weight: 600;
  }
  .rec-label.idle {
    color: #777;
  }
  .text {
    flex: 1;
    font-size: 18px;
    line-height: 1.3;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .seg.partial { color: #aaa; font-style: italic; }
  .seg.final { color: #fff; }
  .seg.formatted { color: #6fe6c1; font-weight: 500; }
  .seg.idle { color: #777; }
  .placeholder { color: #666; font-size: 14px; font-style: italic; }
  @keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.35; transform: scale(0.85); }
  }
</style>
