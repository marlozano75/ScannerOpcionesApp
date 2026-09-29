Aplicación con un panel de control de Cushion del porfolio según mi porfolio en IBKR con mi cuenta de margen Reg T de irlanda y el scanner de opciones basado en una Watchlist. 

La aplicación se debe conectar a TWS a través de su API para recolectar toda la información que necesite. Se puede seleccionar conectarse a la cuenta regular o a la cuenta simulada.

El escaner debe ser capaz de soportar operaciones sobre opciones de varios tipos:
	- Operación Regular: precios de ejercicio (strikes) que estén entre un 20% y 25% por debajo (ajustable) del precio actual del mercado y que tengan un rendimiento de prima bruto (gross premium yield) de al menos el 1% para el período DTE de 25 a 25 días
	- Operación Táctica: precios de ejercicio (strikes) que estén un 10% por debajo (ajustable) del precio actual del mercado y que tengan un rendimiento de prima bruto (gross premium yield) de al menos el 1% para el período DTE de menos de días
	- Debe también permitir filtrar por mínimo OI, max Spread, IV Rank y IV Percentile
	
Se puede introducir la Watchlist con copiar y pegar un conjunto de tickers o cargando un archivo excel o de texto

Una vez al día y para las tickers que se añaden posteriormente a esta ejecución, es necesario actualizar para cada ticker de la watchlist la siguiente información:
		○ Sector
		○ Categoria
		○ Expiraciones
		○ DTE
		○ Strikes
		○ Contratos candidatos
		○ Días hasta fecha exdividendo si aplica
		○ Descargas el historial de Volatilidad Implícita del subyacente a 1 año usando para calcular su IV Rank y el IV Percentile -> Esto se podría guardar y guardar solo las últimas entradas que no se han guaradado ya…si tarda mucho.
Cada X minutos se refrescará además la siguiente información:
		○ Bid
		○ Ask
		○ Delta
		○ IV
		○ Last
		○ OI
		○ Incluir campo con la fecha y hora de la última actulización
		○ Calcular el Spread en %
		○ Yield (Prima por acción dividido entre el precio del strike)
		○ Yield Anualizado 
		○ IV Rank
		○ IV Percentile
		○ Margen inicial si se ejecuta la operación
	
Cuando ejecuto el escaneo se muestran solo los contratos que cumplen los criterios seleccionados

		○ Se añade también información a cada entrada de contrato de cuanto imcrementa el peso del sector si el coge este contrato en función del portfolio actual y el peso respecto al resto de contratos que expiran en la misma semana.
		○ Incluir el % de portfolio que significaría si te asignan al precio de strike.
	
	
Debe permitir seleccionar varios contratos para simular como quedaría la diversificación del porfolio y el riesgo (cushion, margenes, lo que se defina). Se debería mostrar el porfolio actual y el futuro si se ejercieran esos contratos.

Panel de control de riesgo por apalancamiento de la cuenta Reg T
	Tiene que incluir el Cushion de la cartera y un semáforo que indique el nivel de riesgo basado en:
		○ > 40%: Estado Normal / Holgado. Permite soportar volatilidad en el mercado, tener margen de maniobra para ajustar posiciones y mantener una baja probabilidad de liquidación forzada.
		○ 25%-30%: Nivel de Preocupación. Es el umbral donde el inversor debe empezar a prestar atención a la cuenta.
		○ <25%: Riesgo algo. Evaluación Activa de Riesgo. Requiere evaluar posiciones y empezar a recortar riesgo inmediatamente.
	
	Mostrar también los valores de Look Ahead, Overnigh y Post-Expirity de IBKR. También aplicando los valores del semáforo de Cushion de cartera.
			
	Mostrar últimos 5 días de VIX, el VIX actual y el valor previsto de futuros para las próximas 2-3semanas


Panel de Control de diversificación
	- Cada vez que se ejecuta el script actualizar la información de Sectores y % del porfolio invertido en cada sector
	- También incluir la diversificación sectorial para las próximas 5 semanas en las que se tengan contratos abiertos.
