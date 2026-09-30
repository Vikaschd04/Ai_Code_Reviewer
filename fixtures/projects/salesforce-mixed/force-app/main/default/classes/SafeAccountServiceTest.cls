@isTest
private class SafeAccountServiceTest {

    // Negative: creates its own data and asserts the outcome.
    @isTest
    static void trimsLastNames() {
        Account acc = new Account(Name = 'Test');
        insert acc;
        insert new Contact(LastName = ' Smith ', AccountId = acc.Id);
        Test.startTest();
        SafeAccountService.touchContacts(new List<Account>{ acc });
        Test.stopTest();
        Contact c = [SELECT LastName FROM Contact WHERE AccountId = :acc.Id];
        Assert.areEqual('Smith', c.LastName, 'last name is trimmed');
    }
}
