<?php

class srvpanel_launch extends rcube_plugin
{
    public $task = 'login';

    public function init()
    {
        $this->add_hook('loginform_content', [$this, 'loginform_content']);
    }

    private function decode_urlsafe($value)
    {
        $padding = strlen($value) % 4;
        if ($padding) {
            $value .= str_repeat('=', 4 - $padding);
        }
        return base64_decode(strtr($value, '-_', '+/'), true);
    }

    private function token_email($token)
    {
        if (!is_string($token) || substr_count($token, '.') !== 1) {
            return null;
        }
        [$encoded, $provided] = explode('.', $token, 2);
        $secret = @file_get_contents('/run/secrets/srvpanel_launch_secret');
        if ($secret === false || strlen(trim($secret)) < 32) {
            return null;
        }
        $expected = hash_hmac('sha256', $encoded, trim($secret), true);
        $signature = $this->decode_urlsafe($provided);
        if ($signature === false || !hash_equals($expected, $signature)) {
            return null;
        }
        $raw = $this->decode_urlsafe($encoded);
        $payload = $raw === false ? null : json_decode($raw, true);
        if (!is_array($payload) || !isset($payload['email'], $payload['exp'])) {
            return null;
        }
        if (!is_string($payload['email']) || (int) $payload['exp'] < time()) {
            return null;
        }
        if ((int) $payload['exp'] > time() + 60) {
            return null;
        }
        return filter_var($payload['email'], FILTER_VALIDATE_EMAIL)
            ? strtolower($payload['email'])
            : null;
    }

    public function loginform_content($args)
    {
        $email = $this->token_email(rcube_utils::get_input_value('_launch', rcube_utils::INPUT_GET));
        if (!$email) {
            return $args;
        }
        $encoded = json_encode($email, JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT);
        $args['content'] .= '<script>'
            . 'document.addEventListener("DOMContentLoaded",function(){'
            . 'var input=document.querySelector("input[name=_user]");'
            . 'if(input){input.value=' . $encoded . ';'
            . 'var pass=document.querySelector("input[name=_pass]");if(pass){pass.focus();}}'
            . 'if(window.history&&window.history.replaceState){'
            . 'window.history.replaceState(null,document.title,window.location.pathname);}'
            . '});</script>';
        return $args;
    }
}
