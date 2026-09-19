import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist', 'validador_mantenimiento/.venv-ocr/**']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
  },
  {
    files: ['validador_mantenimiento/frontend/js/*.js'],
    languageOptions: {
      globals: {
        API: 'readonly',
        UI: 'readonly',
        resultMarkup: 'readonly',
        MaintenanceAnalysisAPI: 'readonly',
      },
    },
  },
])
