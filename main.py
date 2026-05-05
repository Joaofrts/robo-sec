import gspread
import os
import json
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import nest_asyncio
import asyncio

nest_asyncio.apply()

# 1. Pegando as senhas do cofre do GitHub (Secrets)
USUARIO = os.environ.get("USUARIO_SEI")
SENHA = os.environ.get("SENHA_SEI")
CREDENCIAIS_JSON = os.environ.get("GCP_CREDENTIALS")

# 2. Autenticação invisível no Google Sheets
# Ele transforma o texto JSON oculto de volta em um dicionário para logar
credenciais_dict = json.loads(CREDENCIAIS_JSON)
gc = gspread.service_account_from_dict(credenciais_dict)

# Configurações Iniciais (Substitua pelos seus dados)
URL_SEI = "https://seibahia.ba.gov.br/sip/login.php?sigla_orgao_sistema=GOVBA&sigla_sistema=SEI"
NOME_PLANILHA = "MONITORAMENTO DE CONVÊNIOS"
ABA_PLANILHA = "Convênios"

async def main():
    # 2. Conectando na Planilha
    planilha = gc.open(NOME_PLANILHA).worksheet(ABA_PLANILHA)
    # Supondo que os processos estão na Coluna G (índice 7) a partir da linha 4
    lista_processos = planilha.col_values(7)[3:]
    print(f"Total de processos a verificar: {len(lista_processos)}")

    # 3. Iniciando o Playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        print("Acessando o SEI...")
        await page.goto(URL_SEI)

        # 4. Login no SEI
        print("Preenchendo formulário de login...")
        await page.locator("#txtUsuario").fill(USUARIO)
        await page.locator("#pwdSenha").fill(SENHA)
        await page.locator("#selOrgao").select_option(label="SEC")
        await page.locator("#sbmAcessar").click()
        
        print("Aguardando a barra de pesquisa aparecer...")
        await page.wait_for_selector("#txtPesquisaRapida", state="visible", timeout=60000)
        print("Login realizado com sucesso!")

        # 5. Loop pelos processos da planilha
        for index, processo in enumerate(lista_processos):
            linha_atual = index + 4 # Começa na linha 4 conforme seu corte [3:]
            print(f"\n--- Buscando processo: {processo} ---")

            # Digita o processo e pesquisa
            await page.locator("#txtPesquisaRapida").fill(processo)
            await page.keyboard.press("Enter")

            frame_arvore = page.frame_locator("#ifrArvore")
            
            try:
                # CORREÇÃO 1: A Trava de Sincronização
                # Garante que o menu lateral recarregou e está exibindo o processo ATUAL
                await frame_arvore.get_by_text(processo).first.wait_for(state="visible", timeout=15000)
                
                print("Página recarregada. Procurando o botão de Consultar Andamento...")
                botao_andamento = frame_arvore.locator("#divConsultarAndamento a").first
                await botao_andamento.wait_for(state="visible", timeout=10000)
                await botao_andamento.click()
                print("Histórico solicitado. Lendo os dados...")

                frame_principal = page.frame_locator("#ifrVisualizacao")
                elementos_setores = frame_principal.locator("tr.andamentoAberto a.ancoraSigla")
                
                # CORREÇÃO 2: Tratamento Fino do Timeout
                try:
                    # Espera no máximo 5 segundos pela tabela de andamento (se não aparecer, tá fechado)
                    await elementos_setores.first.wait_for(state="visible", timeout=5000)
                    
                    textos_setores = await elementos_setores.all_inner_texts()
                    encontrou_ceo = False
                    
                    for texto in textos_setores:
                        if "CEO" in texto.upper():
                            encontrou_ceo = True
                            break
                    
                    if encontrou_ceo:
                        print(">> Status: Aberto na CEO")
                        planilha.update_cell(linha_atual, 2, "CEO") 
                    else:
                        print(">> Status: Aberto, mas em outro setor")
                        planilha.update_cell(linha_atual, 1, "Processo não está aberto na CEO")
                        
                except PlaywrightTimeoutError:
                    # Cai aqui APENAS se a classe 'andamentoAberto' não existir (processo concluído/sem andamento)
                    print(">> Status: Sem andamento aberto")
                    planilha.update_cell(linha_atual, 1, "Processo não está aberto na CEO")
                
            except Exception as e:
                # Cai aqui se der um erro grave (o processo não existe, o SEI caiu, etc)
                print(f"!! Erro ao navegar no processo {processo}: {e}")
                planilha.update_cell(linha_atual, 1, "Erro de leitura no SEI")
            
            # CORREÇÃO 3: Pausa para a rede respirar antes da próxima pesquisa
            await page.wait_for_timeout(1000)

        # Fecha o navegador ao terminar
        await browser.close()
        print("\nAutomação finalizada com sucesso!")

# Executa a função principal
if __name__ == "__main__":
    asyncio.run(main())
