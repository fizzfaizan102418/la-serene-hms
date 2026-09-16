from pathlib import Path

path = Path('apps/web/src/ReservationsPMSView.tsx')
text = path.read_text(encoding='utf-8')
text = text.replace("import React, { useMemo, useState } from 'react';", "import React, { useEffect, useMemo, useState } from 'react';", 1)
path.write_text(text, encoding='utf-8')
