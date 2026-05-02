const http = require('http')
const fs = require('fs')
const path = require('path')
const { createServer: createProxy } = require('http')

const DIST = path.join(__dirname, 'frontend', 'dist')
const API_HOST = 'localhost'
const API_PORT = 8000
const PORT = 3000

const MIME = {
  '.html': 'text/html', '.js': 'application/javascript',
  '.css': 'text/css', '.svg': 'image/svg+xml',
  '.png': 'image/png', '.ico': 'image/x-icon', '.json': 'application/json'
}

http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    // proxy to backend
    const opts = { hostname: API_HOST, port: API_PORT, path: req.url, method: req.method, headers: req.headers }
    const proxy = http.request(opts, pr => {
      res.writeHead(pr.statusCode, pr.headers)
      pr.pipe(res)
    })
    proxy.on('error', () => { res.writeHead(502); res.end('{}') })
    req.pipe(proxy)
    return
  }

  let filePath = path.join(DIST, req.url === '/' ? 'index.html' : req.url)
  if (!fs.existsSync(filePath)) filePath = path.join(DIST, 'index.html')

  const ext = path.extname(filePath)
  res.setHeader('Content-Type', MIME[ext] || 'text/plain')
  fs.createReadStream(filePath).pipe(res)
}).listen(PORT, () => {
  console.log(`App running at http://localhost:${PORT}`)
})
