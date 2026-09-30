@isTest(SeeAllData=true)
private class AccountServiceTest {

    // Positive (test isolation): relies on org data and asserts nothing.
    @isTest
    static void renamesAccount() {
        Account acc = [SELECT Id FROM Account LIMIT 1];
        AccountService.renameAccount(acc.Id, 'Renamed');
    }
}
