#!/usr/bin/env node
/**
 * Quick CC-Switch model-prefix repair helper.
 *
 * This script patches the current Codex provider row, proxy_live_backup,
 * provider_endpoints, common_config_codex, provider_health, and the local
 * config.toml model entry. Use it when a proxy expects a provider-scoped
 * model such as cx/gpt-5.5 instead of bare gpt-5.5.
 */

import { DatabaseSync } from 'node:sqlite';
import { copyFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const args = process.argv.slice(2);

function getArg(name, fallback = '') {
  const idx = args.indexOf(`--${name}`);
  return idx >= 0 && idx + 1 < args.length ? args[idx + 1] : fallback;
}

function hasFlag(name) {
  return args.includes(`--${name}`);
}

function usage(exitCode = 0) {
  console.log(`
Usage:
  node scripts/fix_model_prefix.mjs --db <path> --model <model> [options]

Required:
  --db <path>          Path to cc-switch.db
  --model <model>      Exact model id from /v1/models, e.g. "cx/gpt-5.5"

Optional:
  --base-url <url>     Upstream API base URL
  --api-key <key>      API key (prefer --api-key-file or CODEX_PROVIDER_KEY)
  --api-key-file <p>   File containing the API key
  --api-key-env <name> Env var that holds the API key (default: CODEX_PROVIDER_KEY)
  --provider-id <id>   Select a specific Codex provider row
  --provider-name <n>  Select a provider by name
  --codex-home <path>  Codex home directory (default: ~/.codex)
  --config <path>      config.toml path (default: ~/.codex/config.toml)
  --config-base-url <url>
                       Optional config.toml base_url override
  --dry-run            Show changes without writing
  --verbose            Print progress logs
`);
  process.exit(exitCode);
}

function log(message) {
  if (verbose) {
    console.log(message);
  }
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function tomlQuote(value) {
  return `"${String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
}

function renderTomlLines(lines) {
  const compact = [];
  for (const line of lines) {
    if (!line.trim() && (compact.length === 0 || !compact[compact.length - 1].trim())) {
      continue;
    }
    compact.push(line);
  }
  while (compact.length > 0 && !compact[compact.length - 1].trim()) {
    compact.pop();
  }
  return `${compact.join('\n')}\n`;
}

function tomlLines(text) {
  const lines = String(text ?? '').split(/\r?\n/);
  while (lines.length > 0 && lines[lines.length - 1] === '') {
    lines.pop();
  }
  return lines.length === 1 && lines[0] === '' ? [] : lines;
}

function tableExists(conn, name) {
  const row = conn.prepare(
    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1"
  ).get(name);
  return Boolean(row);
}

function tableColumns(conn, name) {
  if (!tableExists(conn, name)) {
    return [];
  }
  return conn.prepare(`PRAGMA table_info("${name.replace(/"/g, '""')}")`).all().map((row) => row.name);
}

function parseSettings(raw) {
  if (raw === null || raw === undefined) {
    return {};
  }
  if (typeof raw === 'object' && !Buffer.isBuffer(raw)) {
    return { ...raw };
  }
  const text = String(raw).trim();
  if (!text) {
    return {};
  }
  try {
    const parsed = JSON.parse(text);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? { ...parsed } : {};
  } catch {
    return { config: text };
  }
}

function replaceFirstTomlKey(text, key, renderedValue) {
  const source = String(text ?? '');
  const re = new RegExp(`^\\s*${escapeRegExp(key)}\\s*=.*$`, 'm');
  if (re.test(source)) {
    return source.replace(re, `${key} = ${renderedValue}`);
  }
  const needsNewline = source.length > 0 && !source.endsWith('\n');
  return `${source}${needsNewline ? '\n' : ''}${key} = ${renderedValue}\n`;
}

function setRootTomlKey(text, key, renderedValue) {
  const lines = tomlLines(text);

  let firstSection = lines.length;
  for (let idx = 0; idx < lines.length; idx += 1) {
    if (lines[idx].trim().startsWith('[')) {
      firstSection = idx;
      break;
    }
  }

  const re = new RegExp(`^\\s*${escapeRegExp(key)}\\s*=`);
  for (let idx = 0; idx < firstSection; idx += 1) {
    if (re.test(lines[idx])) {
      lines[idx] = `${key} = ${renderedValue}`;
      return renderTomlLines(lines);
    }
  }

  lines.splice(firstSection, 0, `${key} = ${renderedValue}`);
  return renderTomlLines(lines);
}

function ensureTomlSection(text, section) {
  const lines = tomlLines(text);
  if (lines.some((line) => line.trim() === `[${section}]`)) {
    return renderTomlLines(lines);
  }
  if (lines.length === 0) {
    return `[${section}]\n`;
  }
  if (lines.length > 0 && lines[lines.length - 1].trim()) {
    lines.push('');
  }
  lines.push(`[${section}]`);
  return renderTomlLines(lines);
}

function setSectionTomlKey(text, section, key, renderedValue) {
  const withSection = ensureTomlSection(text, section);
  const lines = tomlLines(withSection);
  const start = lines.findIndex((line) => line.trim() === `[${section}]`);
  let end = lines.length;
  for (let idx = start + 1; idx < lines.length; idx += 1) {
    const stripped = lines[idx].trim();
    if (stripped.startsWith('[') && stripped.endsWith(']')) {
      end = idx;
      break;
    }
  }

  const re = new RegExp(`^\\s*${escapeRegExp(key)}\\s*=`);
  for (let idx = start + 1; idx < end; idx += 1) {
    if (re.test(lines[idx])) {
      lines[idx] = `${key} = ${renderedValue}`;
      return renderTomlLines(lines);
    }
  }

  lines.splice(end, 0, `${key} = ${renderedValue}`);
  return renderTomlLines(lines);
}

function normalizeCodexConfig(config, { providerName = 'custom', model, baseUrl }) {
  let next = setRootTomlKey(config, 'model_provider', tomlQuote('custom'));
  next = setRootTomlKey(next, 'model', tomlQuote(model));
  next = ensureTomlSection(next, 'model_providers');
  next = setSectionTomlKey(next, 'model_providers.custom', 'name', tomlQuote(providerName || 'custom'));
  next = setSectionTomlKey(next, 'model_providers.custom', 'wire_api', tomlQuote('responses'));
  next = setSectionTomlKey(next, 'model_providers.custom', 'requires_openai_auth', 'true');
  if (baseUrl) {
    next = setSectionTomlKey(next, 'model_providers.custom', 'base_url', tomlQuote(baseUrl.replace(/\/+$/, '')));
  }
  return next;
}

function patchProviderConfig(raw, { providerName, model, baseUrl, apiKey }) {
  const data = parseSettings(raw);
  const auth = data.auth && typeof data.auth === 'object' && !Array.isArray(data.auth) ? { ...data.auth } : {};

  if (apiKey) {
    auth.OPENAI_API_KEY = apiKey;
  }
  if (Object.keys(auth).length > 0) {
    data.auth = auth;
  }

  data.config = normalizeCodexConfig(String(data.config ?? ''), { providerName, model, baseUrl });
  data.commonConfigEnabled = false;
  data.endpointAutoSelect = true;
  data.apiFormat = 'openai_responses';
  return data;
}

function selectProvider(conn) {
  const cols = tableColumns(conn, 'providers');
  if (cols.length === 0) {
    throw new Error('providers table not found');
  }

  const wanted = ['id', 'name', 'settings_config', 'is_current'];
  const selectCols = wanted.filter((col) => cols.includes(col));
  if (!selectCols.includes('id') || !selectCols.includes('settings_config')) {
    throw new Error('providers table is missing required columns');
  }

  const sqlCols = selectCols.map((col) => `"${col.replace(/"/g, '""')}"`).join(', ');
  const queries = [];
  const appTypeClause = cols.includes('app_type') ? "app_type='codex' AND " : '';

  const providerId = getArg('provider-id');
  const providerName = getArg('provider-name');
  if (providerId) {
    queries.push({
      sql: `SELECT ${sqlCols} FROM providers WHERE ${appTypeClause}id=? LIMIT 1`,
      params: [providerId],
    });
  }
  if (providerName) {
    queries.push({
      sql: `SELECT ${sqlCols} FROM providers WHERE ${appTypeClause}name=? LIMIT 1`,
      params: [providerName],
    });
  }
  if (cols.includes('is_current') && cols.includes('app_type')) {
    queries.push({
      sql: `SELECT ${sqlCols} FROM providers WHERE app_type='codex' AND is_current=1 LIMIT 1`,
      params: [],
    });
  } else if (cols.includes('is_current')) {
    queries.push({
      sql: `SELECT ${sqlCols} FROM providers WHERE is_current=1 LIMIT 1`,
      params: [],
    });
  }
  queries.push({
    sql: cols.includes('app_type')
      ? `SELECT ${sqlCols} FROM providers WHERE app_type='codex' LIMIT 1`
      : `SELECT ${sqlCols} FROM providers LIMIT 1`,
    params: [],
  });

  for (const query of queries) {
    const row = conn.prepare(query.sql).get(...query.params);
    if (row) {
      return row;
    }
  }

  throw new Error('no Codex provider row found');
}

function updateProviderEndpoints(conn, providerId, baseUrl, changes) {
  if (!baseUrl || !tableExists(conn, 'provider_endpoints')) {
    return;
  }
  const cols = tableColumns(conn, 'provider_endpoints');
  if (!cols.includes('provider_id') || !cols.includes('app_type') || !cols.includes('url')) {
    return;
  }

  conn.prepare(
    'DELETE FROM provider_endpoints WHERE app_type=? AND provider_id=?'
  ).run('codex', providerId);

  const insertCols = ['provider_id', 'app_type', 'url'];
  const values = [providerId, 'codex', baseUrl.replace(/\/+$/, '')];
  if (cols.includes('added_at')) {
    insertCols.push('added_at');
    values.push(Date.now());
  }

  const sql = `INSERT INTO provider_endpoints (${insertCols.map((col) => `"${col}"`).join(', ')}) VALUES (${insertCols.map(() => '?').join(', ')})`;
  conn.prepare(sql).run(...values);
  changes.push('provider_endpoints replaced');
}

function updateProxyLiveBackup(conn, settings, changes) {
  if (!tableExists(conn, 'proxy_live_backup')) {
    return;
  }
  const cols = tableColumns(conn, 'proxy_live_backup');
  if (!cols.includes('original_config')) {
    return;
  }

  const originalConfig = JSON.stringify(settings);
  const now = new Date().toISOString().replace('T', ' ').replace(/\.\d{3}Z$/, '');

  if (cols.includes('app_type')) {
    const exists = conn.prepare(
      'SELECT 1 FROM proxy_live_backup WHERE app_type=? LIMIT 1'
    ).get('codex');

    if (exists) {
      if (cols.includes('backed_up_at')) {
        conn.prepare(
          'UPDATE proxy_live_backup SET original_config=?, backed_up_at=? WHERE app_type=?'
        ).run(originalConfig, now, 'codex');
      } else {
        conn.prepare(
          'UPDATE proxy_live_backup SET original_config=? WHERE app_type=?'
        ).run(originalConfig, 'codex');
      }
      changes.push('proxy_live_backup.original_config updated');
      return;
    }

    const insertCols = ['app_type', 'original_config'];
    const values = ['codex', originalConfig];
    if (cols.includes('backed_up_at')) {
      insertCols.push('backed_up_at');
      values.push(now);
    }
    const sql = `INSERT INTO proxy_live_backup (${insertCols.map((col) => `"${col}"`).join(', ')}) VALUES (${insertCols.map(() => '?').join(', ')})`;
    conn.prepare(sql).run(...values);
    changes.push('proxy_live_backup.original_config inserted');
  }
}

function updateCommonConfig(conn, settings, changes) {
  if (!tableExists(conn, 'settings')) {
    return;
  }
  const cols = tableColumns(conn, 'settings');
  if (!cols.includes('key') || !cols.includes('value')) {
    return;
  }

  const value = JSON.stringify(settings);
  const result = conn.prepare(
    'UPDATE settings SET value=? WHERE key=?'
  ).run(value, 'common_config_codex');
  if (result.changes > 0) {
    changes.push('settings.common_config_codex updated');
  }
}

function resetHealth(conn, providerId, changes) {
  if (!tableExists(conn, 'provider_health')) {
    return;
  }
  const cols = tableColumns(conn, 'provider_health');
  if (!cols.includes('provider_id')) {
    return;
  }

  if (cols.includes('app_type')) {
    conn.prepare(
      'DELETE FROM provider_health WHERE provider_id=? AND app_type=?'
    ).run(providerId, 'codex');
  } else {
    conn.prepare(
      'DELETE FROM provider_health WHERE provider_id=?'
    ).run(providerId);
  }
  changes.push('provider_health reset');
}

function patchConfigToml(configPath, model, configBaseUrl, dryRun, changes) {
  if (!existsSync(configPath)) {
    return null;
  }

  const current = readFileSync(configPath, 'utf8');
  let next = setRootTomlKey(current, 'model', tomlQuote(model));
  if (configBaseUrl) {
    next = replaceFirstTomlKey(next, 'base_url', tomlQuote(configBaseUrl.replace(/\/+$/, '')));
  }

  if (next === current) {
    return null;
  }

  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15);
  const backupPath = `${configPath}.bak-model-fix-${stamp}`;
  if (!dryRun) {
    copyFileSync(configPath, backupPath);
    writeFileSync(configPath, next, 'utf8');
  }
  changes.push('config.toml updated');
  return dryRun ? null : backupPath;
}

const verbose = hasFlag('verbose');
const dryRun = hasFlag('dry-run');
const dbPath = getArg('db');
const model = getArg('model');
const baseUrl = getArg('base-url') || '';
const configBaseUrl = getArg('config-base-url') || '';
const apiKey = getArg('api-key') || '';
const apiKeyFile = getArg('api-key-file') || '';
const apiKeyEnv = getArg('api-key-env', 'CODEX_PROVIDER_KEY');
const codexHome = getArg('codex-home') || join(process.env.USERPROFILE || process.env.HOME || '~', '.codex');
const configPath = getArg('config') || join(codexHome, 'config.toml');

if (hasFlag('help') || hasFlag('h')) {
  usage(0);
}

if (!dbPath || !model) {
  usage(1);
}

function readApiKey() {
  if (apiKey) {
    return apiKey.trim();
  }
  if (apiKeyFile) {
    return readFileSync(apiKeyFile, 'utf8').trim();
  }
  if (apiKeyEnv) {
    return String(process.env[apiKeyEnv] || '').trim();
  }
  return String(process.env.CODEX_PROVIDER_KEY || '').trim();
}

const resolvedApiKey = readApiKey();
if (verbose && resolvedApiKey) {
  log('API key: present');
}

if (!existsSync(dbPath)) {
  console.error(JSON.stringify({ ok: false, error: `database not found: ${dbPath}` }, null, 2));
  process.exit(1);
}

const conn = new DatabaseSync(dbPath);
conn.exec('PRAGMA foreign_keys = ON');
const changes = [];

let backupPath = null;
let configBackup = null;

try {
  const provider = selectProvider(conn);
  const providerId = String(provider.id);
  const providerName = String(provider.name || getArg('provider-name') || 'custom');
  const settings = patchProviderConfig(provider.settings_config, {
    providerName,
    model,
    baseUrl,
    apiKey: resolvedApiKey,
  });

  if (dryRun) {
    changes.push('providers.settings_config would be updated');
    if (baseUrl && tableExists(conn, 'provider_endpoints')) {
      changes.push('provider_endpoints would be replaced');
    }
    if (tableExists(conn, 'proxy_live_backup')) {
      changes.push('proxy_live_backup.original_config would be updated');
    }
    if (tableExists(conn, 'settings')) {
      changes.push('settings.common_config_codex would be updated if present');
    }
    if (tableExists(conn, 'provider_health')) {
      changes.push('provider_health would be reset');
    }
  } else {
    backupPath = `${dbPath}.bak-configure-provider-${new Date().toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15)}`;
    copyFileSync(dbPath, backupPath);
    conn.exec('BEGIN IMMEDIATE');

    const providerCols = tableColumns(conn, 'providers');
    if (providerCols.includes('is_current') && providerCols.includes('app_type')) {
      conn.prepare('UPDATE providers SET is_current=0 WHERE app_type=? AND id<>?').run('codex', providerId);
    }
    const updateParts = ['settings_config=?'];
    const updateValues = [JSON.stringify(settings)];
    if (providerCols.includes('is_current')) {
      updateParts.push('is_current=1');
    }
    const whereParts = ['id=?'];
    if (providerCols.includes('app_type')) {
      whereParts.unshift('app_type=?');
      updateValues.push('codex');
    }
    updateValues.push(providerId);
    conn.prepare(`UPDATE providers SET ${updateParts.join(', ')} WHERE ${whereParts.join(' AND ')}`).run(
      ...updateValues
    );
    changes.push('providers.settings_config updated');

    updateProviderEndpoints(conn, providerId, baseUrl, changes);
    updateProxyLiveBackup(conn, settings, changes);
    updateCommonConfig(conn, settings, changes);
    resetHealth(conn, providerId, changes);
    conn.exec('COMMIT');
  }

  configBackup = patchConfigToml(configPath, model, configBaseUrl, dryRun, changes);

  const result = {
    ok: true,
    dry_run: dryRun,
    db: dbPath,
    backup: backupPath,
    config_backup: configBackup,
    provider: { id: providerId, name: providerName },
    model,
    base_url: baseUrl || null,
    config_base_url: configBaseUrl || null,
    api_key_present: Boolean(resolvedApiKey),
    changes,
  };
  console.log(JSON.stringify(result, null, 2));
} catch (error) {
  try {
    conn.exec('ROLLBACK');
  } catch {
    // ignore rollback errors
  }
  console.log(JSON.stringify({ ok: false, error: String(error?.message || error) }, null, 2));
  process.exit(1);
} finally {
  conn.close();
}
