import { spawn } from "child_process";
import http from "http";

console.log("Starting Python Telegram Bot...");

const pythonProcess = spawn("python3.11", ["mainfinal.py"], {
  stdio: "inherit",
});

pythonProcess.on("close", (code) => {
  console.log(`Python process exited with code ${code}`);
  process.exit(code || 0);
});

pythonProcess.on("error", (err) => {
  console.error("Failed to start python process:", err);
  process.exit(1);
});

function startHealthServer(port: number) {
  const server = http.createServer((_req, res) => {
    res.writeHead(200, { "Content-Type": "text/plain" });
    res.end("Bot is running\n");
  });

  server.on("error", (err: NodeJS.ErrnoException) => {
    if (err.code === "EADDRINUSE") {
      console.log(`Port ${port} in use, trying ${port + 1}...`);
      startHealthServer(port + 1);
    } else {
      console.error("Health server error:", err);
    }
  });

  server.listen(port, "0.0.0.0", () => {
    console.log(`Health check server listening on port ${port}`);
  });
}

startHealthServer(5000);
