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
        ? 'tls'
        : 'local'
);
$maddy_host = preg_replace(
    '#^[a-z]+://#i',
    '',
    getenv('ROUNDCUBEMAIL_DEFAULT_HOST') ?: 'localhost'
);
$config['default_host'] = $maddy_transport === 'tls'
    ? 'ssl://' . $maddy_host
    : $maddy_host;
$config['default_port'] = $maddy_transport === 'tls' ? 993 : 143;
$config['smtp_server'] = $maddy_transport === 'tls'
    ? 'tls://' . $maddy_host
    : $maddy_host;
$config['smtp_port'] = 587;
