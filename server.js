const express = require('express');
const multer = require('multer');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3000;

const STORAGE_ROOT = process.env.STORAGE_PATH || path.join(__dirname, 'uploads');
const META_FILE = path.join(STORAGE_ROOT, '.meta.json');
const CONFIG_FILE = path.join(STORAGE_ROOT, '.config.json');

if (!fs.existsSync(STORAGE_ROOT)) fs.mkdirSync(STORAGE_ROOT, { recursive: true });

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

function loadMeta() {
  try { if (fs.existsSync(META_FILE)) return JSON.parse(fs.readFileSync(META_FILE, 'utf8')); } catch(e) {}
  return { files: {} };
}
function saveMeta(meta) { fs.writeFileSync(META_FILE, JSON.stringify(meta, null, 2)); }

function loadConfig() {
  try { if (fs.existsSync(CONFIG_FILE)) return JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8')); } catch(e) {}
  return { watchFolders: [], exportTrigger: 'on_save', intervalMinutes: 10, uploadFormat: 'both', apiKey: '', agentLastSeen: null };
}
function saveConfig(config) { fs.writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2)); }
function hashBuffer(buffer) { return crypto.createHash('sha256').update(buffer).digest('hex'); }

function requireApiKey(req, res, next) {
  const config = loadConfig();
  if (!config.apiKey) return next();
  if (req.headers['x-api-key'] !== config.apiKey) return res.status(401).json({ error: 'Unauthorized' });
  next();
}

const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 300 * 1024 * 1024 },
  fileFilter: (req, file, cb) => {
    const ext = path.extname(file.originalname).toLowerCase();
    ['.mp3', '.wav'].includes(ext) ? cb(null, true) : cb(new Error('MP3/WAV only'));
  }
});

app.get('/api/files', (req, res) => {
  const { folder, format } = req.query;
  const meta = loadMeta();
  let files = Object.entries(meta.files).map(([hash, info]) => ({ hash, ...info }));
  if (folder && folder !== 'all') files = files.filter(f => f.folder === folder);
  if (format && format !== 'all') files = files.filter(f => f.format === format.toLowerCase());
  files.sort((a, b) => new Date(b.uploadedAt) - new Date(a.uploadedAt));
  res.json(files);
});

app.get('/api/folders', (req, res) => {
  const meta = loadMeta();
  res.json([...new Set(Object.values(meta.files).map(f => f.folder))].filter(Boolean));
});

app.post('/api/upload', requireApiKey, upload.array('files', 50), (req, res) => {
  const meta = loadMeta();
  const folder = (req.body.folder || 'Uncategorized').trim();
  const results = [];
  for (const file of req.files) {
    const hash = hashBuffer(file.buffer);
    const ext = path.extname(file.originalname).toLowerCase();
    if (meta.files[hash]) { results.push({ name: file.originalname, status: 'duplicate' }); continue; }
    const folderPath = path.join(STORAGE_ROOT, folder);
    if (!fs.existsSync(folderPath)) fs.mkdirSync(folderPath, { recursive: true });
    const safeName = file.originalname.replace(/[^a-zA-Z0-9._\- ]/g, '_');
    fs.writeFileSync(path.join(folderPath, safeName), file.buffer);
    meta.files[hash] = { name: safeName, originalName: file.originalname, folder, format: ext.replace('.',''), size: file.size, uploadedAt: new Date().toISOString(), path: path.join(folder, safeName) };
    results.push({ name: file.originalname, status: 'uploaded', hash });
  }
  saveMeta(meta);
  res.json({ results });
});

app.get('/api/stream/:folder/:filename', (req, res) => {
  const filePath = path.join(STORAGE_ROOT, req.params.folder, req.params.filename);
  if (!fs.existsSync(filePath)) return res.status(404).json({ error: 'File not found' });
  const stat = fs.statSync(filePath);
  const fileSize = stat.size;
  const range = req.headers.range;
  const mimeType = req.params.filename.toLowerCase().endsWith('.mp3') ? 'audio/mpeg' : 'audio/wav';
  if (range) {
    const [s, e] = range.replace(/bytes=/, '').split('-');
    const start = parseInt(s, 10), end = e ? parseInt(e, 10) : fileSize - 1;
    res.writeHead(206, { 'Content-Range': `bytes ${start}-${end}/${fileSize}`, 'Accept-Ranges': 'bytes', 'Content-Length': end - start + 1, 'Content-Type': mimeType });
    fs.createReadStream(filePath, { start, end }).pipe(res);
  } else {
    res.writeHead(200, { 'Content-Length': fileSize, 'Content-Type': mimeType });
    fs.createReadStream(filePath).pipe(res);
  }
});

app.delete('/api/files/:hash', (req, res) => {
  const meta = loadMeta();
  const fileInfo = meta.files[req.params.hash];
  if (!fileInfo) return res.status(404).json({ error: 'Not found' });
  try {
    const fp = path.join(STORAGE_ROOT, fileInfo.path);
    if (fs.existsSync(fp)) fs.unlinkSync(fp);
    delete meta.files[req.params.hash];
    saveMeta(meta);
    res.json({ success: true });
  } catch(e) { res.status(500).json({ error: 'Delete failed' }); }
});

app.patch('/api/files/:hash/move', (req, res) => {
  const meta = loadMeta();
  const fileInfo = meta.files[req.params.hash];
  if (!fileInfo) return res.status(404).json({ error: 'Not found' });
  const { folder } = req.body;
  const newFolderPath = path.join(STORAGE_ROOT, folder);
  if (!fs.existsSync(newFolderPath)) fs.mkdirSync(newFolderPath, { recursive: true });
  fs.renameSync(path.join(STORAGE_ROOT, fileInfo.path), path.join(newFolderPath, fileInfo.name));
  meta.files[req.params.hash].folder = folder;
  meta.files[req.params.hash].path = path.join(folder, fileInfo.name);
  saveMeta(meta);
  res.json({ success: true });
});

app.get('/api/config', (req, res) => res.json(loadConfig()));

app.post('/api/config', (req, res) => {
  const config = loadConfig();
  const allowed = ['watchFolders','exportTrigger','intervalMinutes','uploadFormat','apiKey'];
  allowed.forEach(k => { if (req.body[k] !== undefined) config[k] = req.body[k]; });
  saveConfig(config);
  res.json({ success: true, config });
});

app.post('/api/agent/heartbeat', requireApiKey, (req, res) => {
  const config = loadConfig();
  config.agentLastSeen = new Date().toISOString();
  saveConfig(config);
  res.json({ success: true, config });
});

app.get('/api/status', (req, res) => {
  const meta = loadMeta();
  const config = loadConfig();
  const fileCount = Object.keys(meta.files).length;
  const totalSize = Object.values(meta.files).reduce((sum, f) => sum + (f.size || 0), 0);
  const agentLastSeen = config.agentLastSeen ? new Date(config.agentLastSeen) : null;
  const agentOnline = agentLastSeen && (Date.now() - agentLastSeen.getTime()) < 2 * 60 * 1000;
  res.json({ status: 'running', fileCount, totalSize, agentOnline: !!agentOnline, agentLastSeen: config.agentLastSeen });
});

app.listen(PORT, () => {
  console.log(`SoundBridg running on port ${PORT}`);
  console.log(`Storage: ${STORAGE_ROOT}`);
});

// ─── Config routes ────────────────────────────────────────────────────────────
app.get('/api/config', (req, res) => res.json(loadConfig()));

app.post('/api/config', (req, res) => {
  const config = loadConfig();
  const allowed = ['watchFolders','exportTrigger','intervalMinutes','uploadFormat','apiKey'];
  allowed.forEach(k => { if (req.body[k] !== undefined) config[k] = req.body[k]; });
  saveConfig(config);
  res.json({ success: true, config });
});

app.post('/api/agent/heartbeat', (req, res) => {
  const config = loadConfig();
  config.agentLastSeen = new Date().toISOString();
  saveConfig(config);
  res.json({ success: true, config });
});
