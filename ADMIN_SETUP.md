# Configuracao da area administrativa

O painel publicado no GitHub Pages e estatico. Por isso, confirmacoes e liberacoes precisam ser salvas em um banco online separado. A solucao preparada no codigo usa Supabase:

- o publico le o estado compartilhado;
- somente o usuario administrador autenticado pode gravar alteracoes;
- os botoes de confirmar/liberar aparecem apenas depois do login.

## 1. Criar o projeto no Supabase

1. Acesse https://supabase.com.
2. Crie um projeto novo.
3. Em Authentication > Users, crie o usuario administrador com e-mail e senha.
4. Em SQL Editor, abra o arquivo `supabase/panel-state.sql`.
5. Troque `trocar-pelo-email-admin@exemplo.com` pelo e-mail do administrador.
6. Execute o SQL.

## 2. Configurar o painel

No Supabase, acesse Project Settings > API e copie:

- Project URL
- anon public key

Depois preencha o arquivo `docs/admin-config.js`:

```js
window.FINALS_ADMIN_CONFIG = {
  supabaseUrl: "PROJECT_URL_AQUI",
  supabasePublishableKey: "PUBLISHABLE_KEY_AQUI",
};
```

A chave publishable pode ficar publicada no GitHub Pages porque as regras de seguranca ficam no Row Level Security do Supabase.

## 3. Uso

Depois de publicado:

1. O publico acessa o painel normalmente, sem botoes de acao.
2. O administrador clica em Entrar.
3. Depois do login, os botoes de confirmar/liberar aparecem.
4. Cada alteracao e salva no estado online e passa a aparecer para todos.
