module.exports = {
  apps: [
    {
      name: "slipt-bot",
      script: "main.py",
      interpreter: "/home/ubuntu/slipt_bot/venv/bin/python3",
      watch: false,
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 20,
      min_uptime: "10s",
      out_file: "logs/out.log",
      error_file: "logs/err.log",
      merge_logs: true,
      env: {
        PYTHONUNBUFFERED: "1"
      }
    }
  ]
};
