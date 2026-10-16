# Mesh to CAD 1.0.0

Complemento para Blender 4.2 a 5.x. Convierte una malla de triángulos (STL de internet, de un
programa de CAD o de un escaneo) en un **sólido limpio con caras de verdad**: las caras planas
quedan como un solo polígono, los agujeros y ejes como cilindros exactos, con sus medidas. Y lo
guarda en **STEP** para seguir editándolo en Fusion 360, FreeCAD, Onshape o SolidWorks.
Todo en modo Objeto; el original queda oculto.

## Uso (Vista 3D → N → pestaña Mesh to CAD)

1. Selecciona la pieza y pulsa **Convertir a CAD**. Sale `<pieza>_cad`:
   - gris: cara plana · naranja: eje o redondeo · verde: agujero · rojo: superficie libre
     (lo que no es plano ni cilindro, p. ej. una figura: se queda tal cual).
   - En el panel: cuántas caras planas y cilindros, y los **diámetros de agujeros y ejes**
     (redondeados: 7,96 → 8).
2. **Guardar STEP**: en la carpeta `CAD` (junto al .blend o en Documentos/CAD).

## Ajustes

- **Origen**: Auto, STL de CAD (caras perfectas) o Escaneo / IA (superficie con ruido).
- **Enderezar a los ejes**: gira la pieza a escuadra con X, Y, Z y la apoya en la cama por su
  cara plana más grande.
- **Medidas limpias**: caras a 90° exactos y diámetros redondeados.
- **Colorear lo reconocido**.

## Límites de la 1.0

- Reconoce planos y cilindros. Conos, esferas y superficies libres se quedan como malla.
- En el STEP los cilindros salen facetados (muchas caras planas estrechas): en Fusion se editan
  igual (empujar caras, hacer agujeros nuevos…), pero no puedes cambiar el diámetro de un agujero
  facetado con un clic.
- Escaneos: las aristas redondeadas por el escáner salen como redondeos o caras estrechas extra.
- Mallas de más de 250.000 caras se aligeran antes de analizarlas.
