<?php

$config['smtp_user'] = '%u';
$config['smtp_pass'] = '%p';
$config['login_lc'] = 2;
$config['login_autocomplete'] = 1;
$config['auto_create_user'] = true;
$config['use_https'] = true;
$config['request_path'] = '/';
$config['skin'] = 'elastic';
$config['remote_resources'] = false;

// The installer validates Maddy's live certificate. Use TLS when it is valid
// for the active mail hostname; otherwise keep the hop on Docker's local
// host-gateway without depending on external DNS or a self-signed certificate.
$maddy_transport = getenv('SRV_MADDY_TRANSPORT') ?: (
    preg_match('#^(ssl|tls)://#i', getenv('ROUNDCUBEMAIL_DEFAULT_HOST') ?: '')
        ? 'tls_unverified'
        : 'local'
);
$maddy_host = preg_replace(
    '#^[a-z]+://#i',
    '',
    getenv('ROUNDCUBEMAIL_DEFAULT_HOST') ?: 'localhost'
);
$maddy_uses_tls = $maddy_transport !== 'local';
$config['imap_host'] = $maddy_uses_tls
    ? 'ssl://' . $maddy_host . ':993'
    : $maddy_host . ':143';
$config['smtp_host'] = $maddy_uses_tls
    ? 'tls://' . $maddy_host . ':587'
    : $maddy_host . ':587';
if ($maddy_transport === 'tls_unverified') {
    $local_tls = [
        'verify_peer' => false,
        'verify_peer_name' => false,
        'allow_self_signed' => true,
    ];
    $config['imap_conn_options'] = ['ssl' => $local_tls];
    $config['smtp_conn_options'] = ['ssl' => $local_tls];
}
