// Path: frontend/src/main.tsx
/**
 * Purpose: App bootstrap with MUI theme and root render.
 * Usage: Vite entrypoint.
 */
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './styles.css'
import { CssBaseline, ThemeProvider, createTheme } from '@mui/material'

const theme = createTheme({
  palette: { mode: 'dark' },
  components: {
    MuiButton: {
      styleOverrides: {
        root: { minWidth: 120, height: 40, marginRight: 8 },
      },
      defaultProps: { variant: 'contained', size: 'medium' }
    }
  }
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <App />
    </ThemeProvider>
  </React.StrictMode>
)