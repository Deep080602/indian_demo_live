#!/bin/bash
# Log stdout/stderr to a file for troubleshooting
exec > >(tee -i /var/log/user_data.log) 2>&1
echo "Starting User Data Script..."

# Update and install basic dependencies
apt-get update -y
apt-get upgrade -y
apt-get install -y python3-pip python3-venv python3-full git wget curl unzip nginx

# Install Chrome headless dependencies
apt-get install -y libxss1 libappindicator1 libgconf-2-4 libatk1.0-0 \
    libatk-bridge2.0-0 libgdk-pixbuf2.0-0 libgtk-3-0 libgbm-1 \
    libasound2t64 libnspr4 libnss3 libx11-xcb1 x11-apps

# Download and install Google Chrome
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
apt-get install -y ./google-chrome-stable_current_amd64.deb
rm google-chrome-stable_current_amd64.deb

# Install Chromium Chromedriver matching Chrome version
apt-get install -y chromium-chromedriver

# Clone codebase
git clone ${github_repo} /home/ubuntu/ALGO_PULSE
chown -R ubuntu:ubuntu /home/ubuntu/ALGO_PULSE

# Navigate to project and set up virtualenv
cd /home/ubuntu/ALGO_PULSE/ALGO_PULSE
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Create default .env file
cat << 'EOF' > .env
FLASK_SECRET_KEY="${flask_secret_key}"
CHROMEDRIVER_PATH="/usr/bin/chromedriver"
TRADING_INDICES="NIFTY,SENSEX"
EOF
chown ubuntu:ubuntu .env

# Set up systemd service
cat << 'EOF' > /etc/systemd/system/algopulse.service
[Unit]
Description=Algo Pulse Trading Bot and Dashboard
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/ALGO_PULSE/ALGO_PULSE
ExecStart=/home/ubuntu/ALGO_PULSE/ALGO_PULSE/venv/bin/python demo_trade.py
Restart=always
RestartSec=5
Environment="PORT=8000"
Environment="CHROMEDRIVER_PATH=/usr/bin/chromedriver"

[Install]
WantedBy=multi-user.target
EOF

# Enable and start service
systemctl daemon-reload
systemctl enable algopulse.service
systemctl start algopulse.service

# Configure Nginx as a reverse proxy
cat << 'EOF' > /etc/nginx/sites-available/algopulse
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        proxy_read_timeout 300;
        proxy_connect_timeout 300;
    }
}
EOF

# Activate site, remove default, and restart Nginx
ln -sf /etc/nginx/sites-available/algopulse /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
systemctl restart nginx

echo "User Data Script completed successfully."
