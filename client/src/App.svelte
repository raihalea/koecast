<script>
  import { onMount } from 'svelte';
  import { invoke } from '@tauri-apps/api/core';
  import { listen } from '@tauri-apps/api/event';

  let serverUrl = $state('');
  let status = $state({ state: 'idle' });

  onMount(async () => {
    try {
      const cfg = await invoke('get_config');
      serverUrl = cfg.server_url;
    } catch (e) {
      console.error('get_config failed', e);
    }
    // listen 登録を先に行ってから get_status で初回状態を引く。
    // (listen 登録前に Rust 側が emit したぶんを get_status で取り戻す)
    const unlisten = await listen('connection-status', (event) => {
      status = event.payload;
    });
    try {
      const initial = await invoke('get_status');
      // listen からの新着がすでに来ていれば、それは初期値より新しいので上書きしない。
      if (status.state === 'idle') {
        status = initial;
      }
    } catch (e) {
      console.error('get_status failed', e);
    }
    return () => unlisten();
  });

  function badgeClass(state) {
    if (state === 'ready') return 'ok';
    if (state === 'connecting' || state === 'reconnecting') return 'pending';
    if (state === 'fatal') return 'err';
    return 'idle';
  }
</script>

<main>
  <h1>koecast</h1>
  <p class="hint">段階6-3-b: gateway への接続状態表示のみ。音声・ホットキー・注入は 6-3-c 以降。</p>

  <section>
    <div class="row">
      <span class="label">サーバ:</span>
      <span class="value">{serverUrl || '(loading...)'}</span>
    </div>
    <div class="row">
      <span class="label">状態:</span>
      <span class="badge {badgeClass(status.state)}">{status.state}</span>
    </div>

    {#if status.state === 'ready'}
      <div class="row meta">server: {status.server}</div>
    {:else if status.state === 'reconnecting'}
      <div class="row meta">attempt {status.attempt} / wait {status.wait_secs}s</div>
      <div class="row meta">reason: {status.reason}</div>
    {:else if status.state === 'connecting'}
      <div class="row meta">connecting to {status.url}</div>
    {:else if status.state === 'fatal'}
      <div class="row meta err">{status.reason}</div>
    {/if}
  </section>
</main>

<style>
  main {
    font-family: -apple-system, system-ui, sans-serif;
    padding: 1.5rem;
    max-width: 640px;
  }
  h1 { margin-top: 0; }
  .hint { color: #666; font-size: 0.85em; margin-top: -0.5rem; }
  .row { margin: 0.4rem 0; }
  .label { color: #666; margin-right: 0.5rem; }
  .value { font-family: ui-monospace, SFMono-Regular, monospace; }
  .badge {
    padding: 0.15rem 0.5rem;
    border-radius: 4px;
    font-size: 0.9em;
    font-family: ui-monospace, SFMono-Regular, monospace;
  }
  .badge.ok { background: #c8e6c9; color: #1b5e20; }
  .badge.pending { background: #fff9c4; color: #795548; }
  .badge.err { background: #ffcdd2; color: #b71c1c; }
  .badge.idle { background: #eceff1; color: #455a64; }
  .meta { color: #888; font-size: 0.85em; }
  .meta.err { color: #b71c1c; }
</style>
