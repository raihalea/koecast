<script>
  import { onMount } from 'svelte';
  import { invoke } from '@tauri-apps/api/core';
  import { listen } from '@tauri-apps/api/event';

  // 接続状態
  let serverUrl = $state('');
  let status = $state({ state: 'idle' });

  // 設定編集 (段階6-3-f)
  // 「現在の値」(起動時の値) と「フォーム編集中の値」を別管理して、
  // 保存対象をフォームから取る + 起動時値を「再起動するまで効いている値」として
  // 横に表示する。
  let liveCfg = $state(null);   // 起動時に invoke('get_config') で取得
  let editCfg = $state(null);   // フォーム編集中の値
  let glossaryText = $state(''); // textarea 用文字列 (1行=1語、空行除外)

  let saving = $state(false);
  let saveMessage = $state(null);
  let saveError = $state(null);

  onMount(async () => {
    try {
      const cfg = await invoke('get_config');
      serverUrl = cfg.server_url;
      liveCfg = cfg;
      editCfg = { ...cfg, glossary: [...cfg.glossary] };
      glossaryText = (cfg.glossary ?? []).join('\n');
    } catch (e) {
      console.error('get_config failed', e);
    }

    const unlisten = await listen('connection-status', (event) => {
      status = event.payload;
    });
    try {
      const initial = await invoke('get_status');
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

  function parseGlossary(text) {
    return text
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter((l) => l.length > 0);
  }

  async function onSave() {
    if (!editCfg) return;
    saving = true;
    saveMessage = null;
    saveError = null;
    try {
      const toSave = {
        server_url: editCfg.server_url.trim(),
        hotkey: editCfg.hotkey.trim(),
        glossary: parseGlossary(glossaryText),
      };
      const path = await invoke('save_config', { cfg: toSave });
      saveMessage = `保存しました: ${path}`;
    } catch (e) {
      saveError = String(e);
    } finally {
      saving = false;
    }
  }

  function onReset() {
    if (!liveCfg) return;
    editCfg = { ...liveCfg, glossary: [...liveCfg.glossary] };
    glossaryText = (liveCfg.glossary ?? []).join('\n');
    saveMessage = null;
    saveError = null;
  }

  // 「保存対象のフォーム値」と「現在動作中の値」が違うかどうか
  function dirty() {
    if (!liveCfg || !editCfg) return false;
    if (editCfg.server_url.trim() !== liveCfg.server_url) return true;
    if (editCfg.hotkey.trim() !== liveCfg.hotkey) return true;
    const g = parseGlossary(glossaryText);
    if (g.length !== liveCfg.glossary.length) return true;
    for (let i = 0; i < g.length; i++) if (g[i] !== liveCfg.glossary[i]) return true;
    return false;
  }
</script>

<main>
  <h1>koecast</h1>

  <section>
    <h2>接続状態</h2>
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

  <section>
    <h2>設定</h2>
    {#if editCfg}
      <div class="field">
        <label for="server_url">サーバ URL (Tailscale MagicDNS + port + path)</label>
        <input id="server_url" type="text" bind:value={editCfg.server_url}
               placeholder="ws://gx10-xxxx.tail-xxxxxx.ts.net:8000/v1/dictation" />
      </div>
      <div class="field">
        <label for="hotkey">プッシュトゥトーク (global-hotkey 形式)</label>
        <input id="hotkey" type="text" bind:value={editCfg.hotkey}
               placeholder="Ctrl+Shift+Space" />
        <div class="meta">Mac の Cmd+Option+Space は Spotlight と衝突するので避ける</div>
      </div>
      <div class="field">
        <label for="glossary">用語辞書 (1行に1語、LLM 整形プロンプトに合流)</label>
        <textarea id="glossary" rows="5" bind:value={glossaryText}
                  placeholder={'Bedrock\nLLM\nThreatLens'}></textarea>
        <div class="meta">Riva Word Boosting は ja に効かないので、ここは LLM 用 (検証で確定)</div>
      </div>

      <div class="actions">
        <button onclick={onSave} disabled={saving || !dirty()}>
          {saving ? '保存中...' : '保存'}
        </button>
        <button onclick={onReset} disabled={saving || !dirty()}>
          変更を破棄
        </button>
      </div>

      {#if saveMessage}
        <div class="notice ok">
          {saveMessage}<br />
          <strong>反映にはアプリの再起動が必要です。</strong>
        </div>
      {/if}
      {#if saveError}
        <div class="notice err">保存に失敗: {saveError}</div>
      {/if}
      {#if dirty() && !saveMessage}
        <div class="notice warn">変更が未保存です</div>
      {/if}
    {:else}
      <div class="meta">(設定読み込み中...)</div>
    {/if}
  </section>
</main>

<style>
  main {
    font-family: -apple-system, system-ui, sans-serif;
    padding: 1.5rem;
    max-width: 720px;
  }
  h1 { margin: 0 0 1rem; }
  h2 { margin: 1.5rem 0 0.5rem; font-size: 1.05rem; color: #333; }
  section { border-top: 1px solid #eee; padding-top: 0.5rem; }
  section:first-of-type { border-top: none; padding-top: 0; }

  .row { margin: 0.4rem 0; }
  .label { color: #666; margin-right: 0.5rem; }
  .value { font-family: ui-monospace, SFMono-Regular, monospace; word-break: break-all; }
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

  .field { margin: 0.8rem 0; }
  .field label { display: block; font-size: 0.85em; color: #555; margin-bottom: 0.25rem; }
  .field input[type="text"] {
    width: 100%;
    box-sizing: border-box;
    padding: 0.4rem 0.6rem;
    font-family: ui-monospace, SFMono-Regular, monospace;
    font-size: 0.95em;
    border: 1px solid #ccc;
    border-radius: 4px;
  }
  .field textarea {
    width: 100%;
    box-sizing: border-box;
    padding: 0.4rem 0.6rem;
    font-family: ui-monospace, SFMono-Regular, monospace;
    font-size: 0.95em;
    border: 1px solid #ccc;
    border-radius: 4px;
    resize: vertical;
  }
  .actions { margin: 1rem 0 0.5rem; display: flex; gap: 0.5rem; }
  .actions button {
    padding: 0.4rem 1rem;
    font-size: 0.95em;
    border: 1px solid #ccc;
    background: #f7f7f7;
    border-radius: 4px;
    cursor: pointer;
  }
  .actions button:disabled { opacity: 0.5; cursor: default; }
  .notice {
    margin-top: 0.5rem;
    padding: 0.5rem 0.75rem;
    border-radius: 4px;
    font-size: 0.9em;
  }
  .notice.ok { background: #e8f5e9; color: #1b5e20; }
  .notice.err { background: #ffebee; color: #b71c1c; }
  .notice.warn { background: #fff3e0; color: #6d4c00; }
</style>
