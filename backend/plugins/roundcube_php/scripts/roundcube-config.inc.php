<?php

$config = [];

// Database connection — SQLite by default for zero-overhead native execution
$db_path = getenv('ROUNDCUBE_DB_PATH') ?: '/opt/srv-panel/data/roundcube_php/db/roundcube.db';
$config['db_dsnw'] = 'sqlite:///' . $db_path . '?mode=0646';

// Paths for logs and temporary files
$config['temp_dir'] = getenv('ROUNDCUBE_TEMP_DIR') ?: '/opt/srv-panel/data/roundcube_php/tmp';
$config['log_dir'] = getenv('ROUNDCUBE_LOG_DIR') ?: '/opt/srv-panel/data/roundcube_php/logs';

// DES Encryption key for session cookies (auto-loaded or default)
$des_file = dirname($db_path) . '/des_key.secret';
if (file_exists($des_file) && ($key = trim(file_get_contents($des_file)))) {
    $config['des_key'] = $key;
} else {
    $config['des_key'] = 'rcmail-!srv-panel-key!2026';
}

// Mail server transport settings
$maddy_transport = getenv('SRV_MADDY_TRANSPORT') ?: 'local';
$maddy_host = preg_replace('#^[a-z]+://#i', '', getenv('ROUNDCUBE_DEFAULT_HOST') ?: '127.0.0.1');
$maddy_uses_tls = ($maddy_transport !== 'local');

$config['imap_host'] = $maddy_uses_tls
    ? 'ssl://' . $maddy_host . ':993'
    : $maddy_host . ':143';

$config['smtp_host'] = $maddy_uses_tls
    ? 'tls://' . $maddy_host . ':587'
    : $maddy_host . ':587';

$config['smtp_user'] = '%u';
$config['smtp_pass'] = '%p';

if ($maddy_transport === 'tls_unverified' || $maddy_transport === 'local') {
    $local_tls = [
        'verify_peer' => false,
        'verify_peer_name' => false,
        'allow_self_signed' => true,
    ];
    $config['imap_conn_options'] = ['ssl' => $local_tls];
    $config['smtp_conn_options'] = ['ssl' => $local_tls];
}

// Authentication & Session settings
$config['auto_create_user'] = true;
$config['login_lc'] = 2;
$config['login_autocomplete'] = 1;
$config['session_lifetime'] = (int)(getenv('ROUNDCUBE_SESSION_LIFETIME') ?: 30);

// UI & Presentation
$config['skin'] = getenv('ROUNDCUBE_SKIN') ?: 'elastic';
$config['product_name'] = getenv('ROUNDCUBE_PRODUCT_NAME') ?: 'SRV Webmail';
$config['use_https'] = true;
$config['request_path'] = '/';
$config['remote_resources'] = false;

// Max attachment and message sizes
$config['max_message_size'] = getenv('ROUNDCUBE_MAX_MESSAGE_SIZE') ?: '32M';

// Active Roundcube Plugins
$plugins_str = getenv('ROUNDCUBE_PLUGINS') ?: 'archive,zipdownload,markasjunk,srvpanel_launch';
$config['plugins'] = array_filter(array_map('trim', explode(',', $plugins_str)));
